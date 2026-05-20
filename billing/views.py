"""
Views do app billing.

Fluxo (Stripe Checkout hospedado):
  1. POST /api/billing/signup/   → cria personal no plano GRÁTIS (sem Stripe),
     devolve JWT. (Endpoint público.)
  2. POST /api/billing/subscribe/ → cria uma Checkout Session na Stripe e
     devolve a `url` pro front redirecionar. O personal paga no domínio da
     Stripe e volta pro `success_url`. (Personal logado.)
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

    Cria uma Stripe Checkout Session em modo subscription e devolve a `url`
    da página hospedada. O front faz `window.location.href = url`. Depois do
    pagamento, a Stripe redireciona pro `BILLING_SUCCESS_URL` e a Subscription
    real é criada/atualizada pelos webhooks (`checkout.session.completed`,
    `customer.subscription.*`, `invoice.paid`).
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

        # Pra reusar Customer entre tentativas (e não poluir o Stripe), passa
        # `customer=` se já temos id; senão `customer_email=` e a Stripe cria.
        customer_kwargs = (
            {"customer": sub_row.stripe_customer_id}
            if sub_row.stripe_customer_id
            else {"customer_email": user.email or None}
        )

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=getattr(
                settings,
                "BILLING_SUCCESS_URL",
                "https://coach.fichagym.com/billing/return?session_id={CHECKOUT_SESSION_ID}",
            ),
            cancel_url=getattr(
                settings, "BILLING_CANCEL_URL", "https://coach.fichagym.com/billing"
            ),
            allow_promotion_codes=True,
            locale="pt-BR",
            metadata={"user_id": str(user.id), "plan": plan},
            # Espelha o metadata na Subscription criada pelo Checkout — ajuda o
            # webhook a achar o User quando `customer.subscription.created`
            # chega antes de `checkout.session.completed`.
            subscription_data={"metadata": {"user_id": str(user.id), "plan": plan}},
            **customer_kwargs,
        )

        # Guarda o plan escolhido na row pra o webhook não depender só do
        # metadata da Stripe se ele falhar.
        sub_row.plan = plan
        sub_row.price_id = price_id
        sub_row.save(update_fields=["plan", "price_id", "updated_at"])

        return Response(
            {"url": session.url, "session_id": session.id},
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
            if event_type == "checkout.session.completed":
                _sync_from_checkout(obj)
            elif event_type in (
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
def _ts_to_dt(unix_ts):
    if not unix_ts:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=dt_timezone.utc)


def _resolve_user_from_metadata(metadata: dict):
    """Acha o User pelo metadata.user_id (setado em todas as Checkout Sessions)."""
    from accounts.models import User  # import lazy pra evitar circular

    user_id = (metadata or {}).get("user_id")
    if not user_id:
        return None
    try:
        return User.objects.get(pk=int(user_id))
    except (User.DoesNotExist, ValueError, TypeError):
        return None


def _sync_from_checkout(session: dict) -> None:
    """
    checkout.session.completed → liga a Subscription criada pela Stripe ao
    nosso User via metadata.user_id. Cria a linha local se ainda não existir.
    """
    if session.get("mode") != "subscription":
        return  # Checkout `payment` (one-off) não nos interessa
    user = _resolve_user_from_metadata(session.get("metadata") or {})
    if user is None:
        log.warning("checkout.session.completed sem user_id resolvível")
        return
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if not customer_id or not subscription_id:
        return
    row, _ = Subscription.objects.get_or_create(
        user=user,
        defaults={
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "plan": (session.get("metadata") or {}).get("plan", Subscription.Plan.MONTHLY),
            "status": Subscription.Status.ACTIVE,
        },
    )
    row.stripe_customer_id = customer_id
    row.stripe_subscription_id = subscription_id
    if row.status not in {Subscription.Status.ACTIVE, Subscription.Status.TRIALING}:
        # checkout.session.completed implica pagamento confirmado — vira active.
        # A próxima customer.subscription.* eventualmente confirma e sobrescreve.
        row.status = Subscription.Status.ACTIVE
    row.save()


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
            # último recurso: metadata.user_id (setado pelo subscribe via
            # subscription_data.metadata) — útil quando o webhook chega antes
            # do checkout.session.completed.
            user = _resolve_user_from_metadata(stripe_sub.get("metadata") or {})
            if user is None:
                log.warning("Subscription %s sem linha local — ignorando", sub_id)
                return
            row, _ = Subscription.objects.get_or_create(
                user=user,
                defaults={
                    "stripe_customer_id": stripe_sub.get("customer") or "",
                    "plan": Subscription.Plan.MONTHLY,
                    "status": Subscription.Status.INCOMPLETE,
                },
            )
        row.stripe_subscription_id = sub_id

    if stripe_sub.get("customer"):
        row.stripe_customer_id = stripe_sub.get("customer")
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
