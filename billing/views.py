"""
Views do app billing.

Fluxo (Payment Element / "create subscription default_incomplete"):
  1. POST /api/billing/signup/   → cria personal no plano GRÁTIS (sem Stripe),
     devolve JWT. (Endpoint público.)
  2. POST /api/billing/subscribe/ → cria Customer + Subscription
     `default_incomplete` na Stripe e devolve o `client_secret` pro front
     confirmar com o Payment Element. (Personal logado.)
  3. POST /api/billing/webhook/  → Stripe avisa pagamento/mudanças; aqui é a
     FONTE DA VERDADE do status. (Público + assinatura verificada.)
  4. GET  /api/billing/subscription/ → estado atual (pro paywall/poll).
  5. POST /api/billing/portal/   → sessão do Customer Portal (gerenciar cartão,
     trocar plano, cancelar). (Personal logado.)
"""

import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import IsTrainer
from accounts.serializers import UserSerializer

from .models import Subscription
from .serializers import (
    SubscribeSerializer,
    SubscriptionSerializer,
    TrainerSignupSerializer,
)
from .stripe_gateway import get_stripe, price_id_for_plan, stripe_enabled

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
    POST /api/billing/subscribe/  body: {"plan": "monthly"|"annual"}

    Cria (ou reusa) o Customer da Stripe e abre uma Subscription
    `default_incomplete`. Devolve o `client_secret` pro front confirmar o
    pagamento com o Payment Element.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        body = SubscribeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        plan = body.validated_data["plan"]

        stripe = get_stripe()  # 503 claro se não configurado (modo scaffold)
        price_id = price_id_for_plan(plan)
        user = request.user

        sub_row, _ = Subscription.objects.get_or_create(
            user=user,
            defaults={"plan": plan, "status": Subscription.Status.INCOMPLETE},
        )

        # Reusa o Customer se já existe; senão cria.
        if not sub_row.stripe_customer_id:
            customer = stripe.Customer.create(
                email=user.email or None,
                name=user.display_name or user.username,
                metadata={"user_id": str(user.id)},
            )
            sub_row.stripe_customer_id = customer.id

        stripe_sub = stripe.Subscription.create(
            customer=sub_row.stripe_customer_id,
            items=[{"price": price_id}],
            payment_behavior="default_incomplete",
            payment_settings={"save_default_payment_method": "on_subscription"},
            expand=["latest_invoice.confirmation_secret", "pending_setup_intent"],
            billing_mode={"type": "flexible"},
            metadata={"user_id": str(user.id)},
        )

        sub_row.stripe_subscription_id = stripe_sub.id
        sub_row.plan = plan
        sub_row.price_id = price_id
        sub_row.status = stripe_sub.status
        sub_row.save()

        client_secret = _extract_client_secret(stripe_sub)
        return Response(
            {
                "client_secret": client_secret,
                "subscription_id": stripe_sub.id,
                "publishable_key": getattr(settings, "STRIPE_PUBLISHABLE_KEY", ""),
            },
            status=status.HTTP_200_OK,
        )


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
            "stripe_enabled": stripe_enabled(),
            "subscription": None,
        }
        sub = Subscription.objects.filter(user=user).first()
        if sub:
            payload["subscription"] = SubscriptionSerializer(sub).data
        return Response(payload)


class BillingPortalView(APIView):
    """
    POST /api/billing/portal/ — cria sessão do Customer Portal e devolve a URL.

    O portal hospedado da Stripe cobre trocar cartão, mudar plano e cancelar
    sem precisarmos construir UI pra isso.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        stripe = get_stripe()
        sub = Subscription.objects.filter(user=request.user).first()
        if not sub or not sub.stripe_customer_id:
            return Response(
                {"detail": "Você ainda não tem uma assinatura pra gerenciar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return_url = request.data.get("return_url") or getattr(
            settings, "BILLING_PORTAL_RETURN_URL", "https://coach.fichagym.com/me"
        )
        portal = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id, return_url=return_url
        )
        return Response({"url": portal.url})


class StripeWebhookView(APIView):
    """
    POST /api/billing/webhook/ — recebe eventos da Stripe.

    Verifica a assinatura com STRIPE_WEBHOOK_SECRET e usa `request.body` cru
    (NÃO `request.data`, que re-serializa e quebra a assinatura). Idempotente:
    sempre copia o estado atual do objeto Stripe.
    """

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()  # webhook não é autenticado por JWT

    def post(self, request):
        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            return Response(
                {"detail": "Webhook não configurado."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        stripe = get_stripe()
        sig = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        try:
            event = stripe.Webhook.construct_event(request.body, sig, secret)
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)  # payload inválido
        except stripe.error.SignatureVerificationError:
            return Response(status=status.HTTP_400_BAD_REQUEST)  # assinatura inválida

        event_type = event["type"]
        obj = event["data"]["object"]

        try:
            if event_type in (
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
            ):
                _sync_from_subscription(obj)
            elif event_type == "invoice.paid":
                _sync_from_invoice(obj, fallback_status=Subscription.Status.ACTIVE)
            elif event_type == "invoice.payment_failed":
                _sync_from_invoice(
                    obj, fallback_status=Subscription.Status.PAST_DUE
                )
            else:
                log.info("Webhook Stripe ignorado: %s", event_type)
        except Exception:  # nunca devolve 500 pra Stripe — ela re-tenta em loop
            log.exception("Erro processando webhook %s", event_type)

        return Response(status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Helpers de webhook
# ---------------------------------------------------------------------------
def _extract_client_secret(stripe_sub) -> str | None:
    """
    Com billing_mode flexible (API 2025-06-30.basil+), o secret vem em
    `latest_invoice.confirmation_secret.client_secret`. Em casos com trial,
    pode vir um `pending_setup_intent`.
    """
    pending = getattr(stripe_sub, "pending_setup_intent", None)
    if pending:
        return pending.get("client_secret") if isinstance(pending, dict) else getattr(pending, "client_secret", None)
    invoice = getattr(stripe_sub, "latest_invoice", None)
    if not invoice:
        return None
    confirmation = invoice.get("confirmation_secret") if isinstance(invoice, dict) else getattr(invoice, "confirmation_secret", None)
    if confirmation:
        return confirmation.get("client_secret") if isinstance(confirmation, dict) else getattr(confirmation, "client_secret", None)
    # Fallback p/ integrações em billing_mode clássico
    pi = invoice.get("payment_intent") if isinstance(invoice, dict) else getattr(invoice, "payment_intent", None)
    if isinstance(pi, dict):
        return pi.get("client_secret")
    return getattr(pi, "client_secret", None) if pi else None


def _ts_to_dt(unix_ts):
    if not unix_ts:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=dt_timezone.utc)


def _sync_from_subscription(stripe_sub: dict) -> None:
    """Copia status/period/cancel da Subscription da Stripe pra nossa linha."""
    sub_id = stripe_sub.get("id")
    row = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
    if row is None:
        # tenta achar pelo customer (ex.: created chegou antes de salvarmos o id)
        row = Subscription.objects.filter(
            stripe_customer_id=stripe_sub.get("customer")
        ).first()
        if row is None:
            log.warning("Subscription %s sem linha local — ignorando", sub_id)
            return
        row.stripe_subscription_id = sub_id

    row.status = stripe_sub.get("status", row.status)
    row.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end"))
    row.current_period_end = _ts_to_dt(stripe_sub.get("current_period_end"))
    items = (stripe_sub.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        if price.get("id"):
            row.price_id = price["id"]
    row.save()


def _sync_from_invoice(invoice: dict, *, fallback_status: str) -> None:
    """invoice.paid / invoice.payment_failed → atualiza a linha pelo subscription."""
    sub_id = invoice.get("subscription")
    if not sub_id:
        return
    row = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
    if row is None:
        log.warning("Invoice da subscription %s sem linha local", sub_id)
        return
    row.status = fallback_status
    row.save(update_fields=["status", "updated_at"])
