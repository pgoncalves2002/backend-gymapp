"""
Views do app billing (assinatura do PERSONAL pelo uso do app).

Fluxo (Asaas):
  1. POST /api/billing/signup/      → cria personal no plano GRÁTIS (sem
     Asaas), devolve JWT. (Endpoint público.)
  2. POST /api/billing/subscribe/   → cria Customer (se preciso) + Subscription
     no Asaas e devolve `invoiceUrl` (página de pagamento hospedada). O front
     faz `window.location.href = url`. (Personal logado.)
  3. POST /api/billing/webhook/     → Asaas avisa pagamento/mudanças; aqui é
     a FONTE DA VERDADE do status. (Público + token verificado.)
  4. GET  /api/billing/subscription/ → estado atual (pro paywall/poll).
  5. POST /api/billing/cancel/      → cancela a Subscription do Asaas. Como o
     Asaas não tem Customer Portal hospedado, ficamos com endpoint próprio
     (substitui o BillingPortalView da Stripe).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import IsTrainer
from accounts.serializers import UserSerializer

from . import asaas_gateway
from .models import Subscription
from .serializers import (
    SubscribeSerializer,
    SubscriptionSerializer,
    TrainerSignupSerializer,
)

log = logging.getLogger(__name__)


def _tokens_for(user) -> dict:
    """Mesma forma do login: access + refresh + user."""
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    refresh["display_name"] = user.display_name or user.username
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user).data,
    }


def _value_reais_for_plan(plan: str) -> float:
    """
    Resolve o valor (em reais) no servidor — NUNCA confiar em valor vindo do
    front. Em modo scaffold (settings sem chave) ainda funciona com defaults.
    """
    prices = getattr(settings, "ASAAS_PRICES", {})
    cents = int(prices.get(plan) or 0)
    if cents <= 0:
        raise asaas_gateway.AsaasNotConfigured(
            f"Valor do plano '{plan}' não configurado em ASAAS_PRICES."
        )
    return round(cents / 100.0, 2)


def _next_due_date_iso(days_from_today: int = 1) -> str:
    """O Asaas exige `nextDueDate`/`dueDate` no formato 'YYYY-MM-DD'."""
    from datetime import date, timedelta

    return (date.today() + timedelta(days=days_from_today)).isoformat()


class TrainerSignupView(APIView):
    """POST /api/billing/signup/ — cadastro grátis de personal (sem cartão)."""

    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        serializer = TrainerSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(_tokens_for(user), status=status.HTTP_201_CREATED)


class SubscribeView(APIView):
    """
    POST /api/billing/subscribe/  body: {"plan": "monthly"|"annual", "cpf_cnpj"?: "..."}

    Cria (ou reutiliza) o Customer e a Subscription no Asaas, e devolve a
    `invoiceUrl` hospedada. O front faz `window.location.href = url`. Depois
    do pagamento, o webhook do Asaas (`PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`)
    atualiza o status local.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        body = SubscribeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        plan = body.validated_data["plan"]
        cpf_cnpj = (body.validated_data.get("cpf_cnpj") or "").strip() or None

        asaas_gateway.ensure_enabled()  # 503 se sem chave (modo scaffold)
        value_reais = _value_reais_for_plan(plan)
        user = request.user

        sub_row, _ = Subscription.objects.get_or_create(
            user=user,
            defaults={"plan": plan, "status": Subscription.Status.INCOMPLETE},
        )

        # 1. Customer — reaproveita se já existe.
        if not sub_row.asaas_customer_id:
            customer = asaas_gateway.create_customer(
                name=user.display_name or user.username,
                email=user.email or None,
                cpf_cnpj=cpf_cnpj,
                mobile_phone=user.phone or None,
                external_reference=f"user_{user.id}",
            )
            sub_row.asaas_customer_id = customer["id"]

        # 2. Subscription — sempre cria nova quando o front bate aqui (o
        # personal pode tentar de novo se a anterior falhou). Asaas não
        # reaproveita subscriptions canceladas.
        cycle = "MONTHLY" if plan == Subscription.Plan.MONTHLY else "YEARLY"
        sub = asaas_gateway.create_subscription(
            customer_id=sub_row.asaas_customer_id,
            value_reais=value_reais,
            cycle=cycle,
            next_due_date=_next_due_date_iso(days_from_today=1),
            billing_type="UNDEFINED",  # deixa o pagador escolher PIX/CARD/BOLETO
            description=(
                f"FichaGym — assinatura {plan} (personal trainer)"
            ),
            external_reference=f"user_{user.id}",
        )

        sub_row.asaas_subscription_id = sub["id"]
        sub_row.plan = plan
        sub_row.price_cents = int(round(value_reais * 100))
        invoice_url = _first_invoice_url(sub) or ""
        sub_row.last_invoice_url = invoice_url
        sub_row.save()

        return Response(
            {
                "url": invoice_url,
                "subscription_id": sub["id"],
            },
            status=status.HTTP_200_OK,
        )


def _first_invoice_url(sub: dict) -> str | None:
    """
    `POST /v3/subscriptions` devolve a subscription mas o link pra pagar a
    PRIMEIRA fatura está em `/v3/subscriptions/{id}/payments` (ou no objeto
    Payment criado em background). Pra simplificar, tentamos:

      1. campo `invoiceUrl` direto (algumas versões da API retornam).
      2. listar `/subscriptions/{id}/payments` e pegar o `invoiceUrl` do
         primeiro pendente.

    Em scaffold/sandbox sem chave válida o segundo passo falha; nesse caso
    devolvemos vazio e o front mostra "Pagamento em configuração".
    """
    direct = sub.get("invoiceUrl")
    if direct:
        return direct
    sub_id = sub.get("id")
    if not sub_id:
        return None
    try:
        payments = asaas_gateway.request(
            "GET", f"/subscriptions/{sub_id}/payments"
        )
    except asaas_gateway.AsaasError:
        return None
    items = payments.get("data") or []
    for p in items:
        url = p.get("invoiceUrl")
        if url:
            return url
    return None


class SubscriptionDetailView(APIView):
    """
    GET /api/billing/subscription/

    Estado atual da assinatura do personal logado. Se ele não tem assinatura,
    devolve o "plano grátis" com o limite e o uso atual — pro front montar o
    paywall e a tela de upgrade.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request):
        user = request.user
        free_limit = getattr(settings, "FREE_STUDENT_LIMIT", 1)
        payload = {
            "has_active_subscription": user.has_active_subscription,
            "is_billing_exempt": user.is_billing_exempt,
            "free_student_limit": free_limit,
            "student_count": user.student_count,
            "asaas_enabled": asaas_gateway.asaas_enabled(),
            "subscription": None,
        }
        sub = Subscription.objects.filter(user=user).first()
        if sub:
            payload["subscription"] = SubscriptionSerializer(sub).data
        return Response(payload)


class SyncSubscriptionView(APIView):
    """
    POST /api/billing/sync/

    Força um GET na subscription/payments do Asaas e atualiza o status local.
    Útil em dev (sem túnel de webhook) e como fallback se um webhook se
    perder. Idempotente.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        sub = Subscription.objects.filter(user=request.user).first()
        if not sub or not sub.asaas_subscription_id:
            return Response(
                {"detail": "Sem assinatura pra sincronizar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        asaas_gateway.ensure_enabled()
        try:
            asaas_sub = asaas_gateway.get_subscription(sub.asaas_subscription_id)
            payments = asaas_gateway.request(
                "GET", f"/subscriptions/{sub.asaas_subscription_id}/payments"
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)

        # Mapeia o status da subscription.
        new_status = sub.status
        asaas_status = (asaas_sub.get("status") or "").upper()
        if asaas_status == "ACTIVE":
            new_status = Subscription.Status.ACTIVE
        elif asaas_status == "EXPIRED":
            new_status = Subscription.Status.PAST_DUE
        elif asaas_status == "INACTIVE":
            new_status = Subscription.Status.CANCELED

        # Se alguma fatura está paga, força active.
        for p in payments.get("data") or []:
            ps = (p.get("status") or "").upper()
            if ps in {"CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"}:
                new_status = Subscription.Status.ACTIVE
                break
            if ps == "OVERDUE":
                new_status = Subscription.Status.PAST_DUE

        sub.status = new_status
        next_due = _parse_iso_date(asaas_sub.get("nextDueDate"))
        if next_due:
            sub.current_period_end = next_due
        sub.save()
        return Response(SubscriptionSerializer(sub).data)


class CancelSubscriptionView(APIView):
    """
    POST /api/billing/cancel/  — cancela a Subscription do Asaas.

    O Asaas não tem Customer Portal hospedado (como a Stripe), então
    substituímos por um endpoint próprio: marca `cancel_at_period_end=True`
    no Asaas, espelha localmente, e o webhook eventualmente confirma.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        sub = Subscription.objects.filter(user=request.user).first()
        if not sub or not sub.asaas_subscription_id:
            return Response(
                {"detail": "Você ainda não tem uma assinatura ativa pra cancelar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        asaas_gateway.ensure_enabled()
        try:
            asaas_gateway.cancel_subscription(sub.asaas_subscription_id)
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        sub.status = Subscription.Status.CANCELED
        sub.cancel_at_period_end = True
        sub.save(update_fields=["status", "cancel_at_period_end", "updated_at"])
        return Response({"detail": "Assinatura cancelada."})


class AsaasWebhookView(APIView):
    """
    POST /api/billing/webhook/ — recebe eventos do Asaas.

    Autenticação: o Asaas envia um token no header `asaas-access-token`
    (configurado por nós no painel quando registramos a URL). Sem assinatura
    HMAC — é um shared secret. Comparação tem que ser constante-tempo.

    Idempotente: o handler sempre copia o estado do objeto recebido. O Asaas
    também manda `event` e `payment`/`subscription` no body.
    """

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()  # webhook não é autenticado por JWT

    def post(self, request):
        import hmac
        import json

        expected = getattr(settings, "ASAAS_WEBHOOK_TOKEN", "")
        if not expected:
            return Response(
                {"detail": "Webhook não configurado."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Asaas: header `asaas-access-token` (case-insensitive em request.headers).
        received = request.headers.get("asaas-access-token") or request.META.get(
            "HTTP_ASAAS_ACCESS_TOKEN", ""
        )
        if not hmac.compare_digest(received or "", expected):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event = payload.get("event") or ""
        try:
            if event.startswith("PAYMENT_"):
                _handle_payment_event(event, payload.get("payment") or {})
            elif event.startswith("SUBSCRIPTION_"):
                _handle_subscription_event(event, payload.get("subscription") or {})
            else:
                log.info("Webhook Asaas ignorado: %s", event)
        except Exception:  # nunca devolve 500 ao Asaas — ele re-tenta em loop
            log.exception("Erro processando webhook Asaas %s", event)

        return Response(status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Helpers de webhook
# ---------------------------------------------------------------------------
def _parse_iso_date(value: str | None):
    if not value:
        return None
    try:
        # Asaas devolve "YYYY-MM-DD" pros vencimentos e ISO completo em
        # `dateCreated`. Normaliza pra datetime UTC.
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value).replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def _find_subscription_row(payment_or_sub: dict) -> Subscription | None:
    """Resolve a linha local por subscription/customer/externalReference."""
    sub_id = payment_or_sub.get("subscription") or payment_or_sub.get("id")
    if sub_id:
        row = Subscription.objects.filter(asaas_subscription_id=sub_id).first()
        if row:
            return row
    customer = payment_or_sub.get("customer")
    if customer:
        row = Subscription.objects.filter(asaas_customer_id=customer).first()
        if row:
            return row
    ext = payment_or_sub.get("externalReference") or ""
    if ext.startswith("user_"):
        from accounts.models import User as _User

        try:
            uid = int(ext.split("_", 1)[1])
        except ValueError:
            return None
        return Subscription.objects.filter(user_id=uid).first()
    return None


def _handle_payment_event(event: str, payment: dict) -> None:
    """
    Atualiza a Subscription a partir de um Payment do Asaas.

    Eventos relevantes:
      - PAYMENT_CONFIRMED / PAYMENT_RECEIVED → status ACTIVE.
      - PAYMENT_OVERDUE                      → status PAST_DUE.
      - PAYMENT_REFUNDED / PAYMENT_DELETED   → não muda status diretamente
        (cancelamento vem via SUBSCRIPTION_*).
    """
    row = _find_subscription_row(payment)
    if row is None:
        log.warning("Webhook payment %s sem subscription local", event)
        return
    if event in {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}:
        row.status = Subscription.Status.ACTIVE
        period_end = _parse_iso_date(payment.get("dueDate"))
        if period_end:
            row.current_period_end = period_end
    elif event == "PAYMENT_OVERDUE":
        row.status = Subscription.Status.PAST_DUE
    elif event in {"PAYMENT_DELETED"}:
        # Não dispara cancelamento — o Asaas avisa via SUBSCRIPTION_DELETED.
        return
    row.save()


def _handle_subscription_event(event: str, sub: dict) -> None:
    row = _find_subscription_row(sub)
    if row is None:
        log.warning("Webhook subscription %s sem linha local", event)
        return
    if event == "SUBSCRIPTION_DELETED":
        row.status = Subscription.Status.CANCELED
        row.cancel_at_period_end = True
    elif event in {"SUBSCRIPTION_CREATED", "SUBSCRIPTION_UPDATED"}:
        next_due = _parse_iso_date(sub.get("nextDueDate"))
        if next_due:
            row.current_period_end = next_due
    row.save()
