"""
Views do app payments (split — personal cobra aluno, FichaGym fica com %).

Fluxo:
  1. POST /api/payments/connect/onboard/    → personal cadastra a subconta
     Asaas (CPF/CNPJ + endereço). Cria via `POST /v3/accounts`.
  2. GET  /api/payments/connect/status/     → estado da subconta (front).
  3. POST /api/payments/students/{id}/billing/ → cria cobrança/subscription
     do aluno COM SPLIT (`PLATFORM_FEE_PERCENT` pra FichaGym, resto vai
     pra `wallet_id` do personal).
  4. GET  /api/payments/students/{id}/billing/ → estado da cobrança.
  5. POST /api/payments/students/{id}/billing/refund/ → estorno.
  6. GET  /api/payments/me/billing/         → o aluno vê quanto/como paga.
  7. POST /api/payments/webhook/connect/    → eventos de cobranças do split.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsTrainer
from billing import asaas_gateway

from .models import ConnectedAccount, StudentBilling
from .serializers import (
    ConnectedAccountStatusSerializer,
    CreateStudentBillingSerializer,
    OnboardSerializer,
    RefundSerializer,
    StudentBillingSerializer,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_can_receive(trainer: User) -> ConnectedAccount:
    """
    Bloqueia cobrança se: trainer não tem `uses_internal_payment` OU não tem
    subconta pronta OU o aluno (verificado fora) não tem `uses_internal_payment`.
    """
    if not trainer.uses_internal_payment:
        raise PermissionDenied(
            "Você ainda não está habilitado a usar o pagamento interno. "
            "Fale com o admin pra liberar."
        )
    ca: ConnectedAccount | None = getattr(trainer, "connected_account", None)
    if ca is None or not ca.is_ready:
        raise PermissionDenied(
            "Cadastre sua conta de recebimento Asaas antes de cobrar alunos."
        )
    return ca


def _split_for(trainer_ca: ConnectedAccount) -> list[dict]:
    """
    Monta o array `split` da cobrança. A FichaGym fica com
    `PLATFORM_FEE_PERCENT` da master; o resto vai pra wallet do personal.

    No Asaas, somar `percentualValue` < 100 — o restante fica na conta master
    (= a FichaGym). Por isso passamos APENAS o split do personal, e a fee é
    o complemento.
    """
    fee = float(getattr(settings, "PLATFORM_FEE_PERCENT", 5.0))
    trainer_pct = round(100.0 - fee, 4)
    if trainer_pct <= 0 or not trainer_ca.wallet_id:
        return []
    return [
        {
            "walletId": trainer_ca.wallet_id,
            "percentualValue": trainer_pct,
        }
    ]


def _next_due_iso(days: int = 1) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value).replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Connect (subconta do personal)
# ---------------------------------------------------------------------------
class OnboardConnectView(APIView):
    """POST /api/payments/connect/onboard/ — cria a subconta Asaas do personal."""

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        body = OnboardSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data

        user: User = request.user
        if not user.uses_internal_payment:
            raise PermissionDenied(
                "Você ainda não está habilitado a usar o pagamento interno."
            )

        # Cria ou recupera a linha local (idempotente).
        ca = ConnectedAccount.objects.filter(user=user).first()
        if ca and ca.asaas_account_id:
            return Response(
                {"detail": "Subconta já cadastrada.", "is_ready": ca.is_ready},
                status=status.HTTP_200_OK,
            )

        asaas_gateway.ensure_enabled()  # 503 em scaffold

        # Asaas /v3/accounts — campos mínimos: name, email, cpfCnpj,
        # mobilePhone, address, addressNumber, postalCode (CEP).
        address_payload = {
            "address": data["address"],
            "addressNumber": data["address_number"],
            "postalCode": data["postal_code"],
        }
        if data.get("complement"):
            address_payload["complement"] = data["complement"]
        if data.get("province"):
            address_payload["province"] = data["province"]

        resp = asaas_gateway.create_subaccount(
            name=user.display_name or user.username,
            email=user.email or "",
            cpf_cnpj=data["cpf_cnpj"],
            mobile_phone=user.phone or None,
            address=address_payload,
            company_type=data.get("company_type") or None,
            birth_date=(
                data["birth_date"].isoformat() if data.get("birth_date") else None
            ),
            incoming_transfer_pix_key=data.get("pix_key") or None,
        )

        ca, _ = ConnectedAccount.objects.get_or_create(user=user)
        ca.asaas_account_id = resp.get("id", "")
        ca.wallet_id = resp.get("walletId", "")
        # apiKey vem na resposta SÓ aqui; guardamos pra eventual White Label.
        # TODO: criptografar antes de prod. Por ora vai em claro só pra MVP.
        api_key = resp.get("apiKey") or ""
        if api_key:
            ca.api_key_encrypted = api_key
        # Conservador: marcamos onboarding como concluído, mas only `can_receive`
        # quando o webhook account.* confirmar (ou polling no /v3/accounts/{id}).
        ca.onboarding_completed = True
        ca.can_receive = bool(ca.wallet_id)  # MVP: wallet existe == pode receber
        ca.save()

        return Response(
            ConnectedAccountStatusSerializer(ca).data,
            status=status.HTTP_201_CREATED,
        )


class ConnectStatusView(APIView):
    """GET /api/payments/connect/status/ — pro SPA mostrar o estado do onboarding."""

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request):
        ca = ConnectedAccount.objects.filter(user=request.user).first()
        if not ca:
            return Response(
                {
                    "exists": False,
                    "is_ready": False,
                    "asaas_enabled": asaas_gateway.asaas_enabled(),
                    "uses_internal_payment": request.user.uses_internal_payment,
                }
            )
        payload = ConnectedAccountStatusSerializer(ca).data
        payload["exists"] = True
        payload["asaas_enabled"] = asaas_gateway.asaas_enabled()
        payload["uses_internal_payment"] = request.user.uses_internal_payment
        return Response(payload)


# ---------------------------------------------------------------------------
# Cobrança do aluno (com split)
# ---------------------------------------------------------------------------
class StudentBillingView(APIView):
    """
    /api/payments/students/{student_id}/billing/

    POST: cria a cobrança/subscription do aluno COM SPLIT.
    GET:  estado atual.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def _get_student(self, request, student_id: int) -> User:
        student = get_object_or_404(
            User, pk=student_id, role=User.Role.STUDENT, created_by=request.user
        )
        return student

    def get(self, request, student_id: int):
        student = self._get_student(request, student_id)
        sb = StudentBilling.objects.filter(student=student).first()
        if not sb:
            return Response({"exists": False})
        payload = StudentBillingSerializer(sb).data
        payload["exists"] = True
        return Response(payload)

    def post(self, request, student_id: int):
        student = self._get_student(request, student_id)
        body = CreateStudentBillingSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data

        # Validação do gating de pagamento interno.
        if not student.uses_internal_payment:
            raise ValidationError(
                {"student": "Habilite 'Pagamento interno' no aluno antes de cobrar."}
            )
        trainer_ca = _ensure_can_receive(request.user)

        asaas_gateway.ensure_enabled()
        value_reais = round(data["price_cents"] / 100.0, 2)
        description = (
            data.get("description")
            or f"Mensalidade — {request.user.display_name or request.user.username}"
        )
        split = _split_for(trainer_ca)
        cpf_cnpj = (data.get("cpf_cnpj") or "").strip() or None

        sb, _ = StudentBilling.objects.get_or_create(
            student=student,
            defaults={
                "trainer": request.user,
                "price_cents": data["price_cents"],
                "mode": data["mode"],
            },
        )

        # Customer no Asaas (sempre na conta master, pq o split é via walletId).
        if not sb.asaas_customer_id:
            customer = asaas_gateway.create_customer(
                name=student.display_name or student.username,
                email=student.email or None,
                cpf_cnpj=cpf_cnpj,
                mobile_phone=student.phone or None,
                external_reference=f"student_{student.id}",
            )
            sb.asaas_customer_id = customer["id"]

        if data["mode"] == StudentBilling.Mode.RECURRING:
            sub = asaas_gateway.create_subscription(
                customer_id=sb.asaas_customer_id,
                value_reais=value_reais,
                cycle="MONTHLY",
                next_due_date=_next_due_iso(1),
                billing_type="UNDEFINED",
                description=description,
                external_reference=f"sb_{sb.id}",
                split=split,
            )
            sb.asaas_subscription_id = sub.get("id", "")
            # Tenta extrair invoiceUrl da 1ª fatura.
            from billing.views import _first_invoice_url

            invoice_url = _first_invoice_url(sub) or ""
            sb.last_invoice_url = invoice_url
        else:
            payment = asaas_gateway.create_payment(
                customer_id=sb.asaas_customer_id,
                value_reais=value_reais,
                due_date=_next_due_iso(7),
                billing_type="UNDEFINED",
                description=description,
                external_reference=f"sb_{sb.id}",
                split=split,
            )
            sb.asaas_payment_id = payment.get("id", "")
            sb.last_invoice_url = payment.get("invoiceUrl", "")

        sb.trainer = request.user
        sb.price_cents = data["price_cents"]
        sb.mode = data["mode"]
        if sb.status == StudentBilling.Status.CANCELED:
            sb.status = StudentBilling.Status.PENDING  # nova tentativa
        sb.save()

        payload = StudentBillingSerializer(sb).data
        payload["url"] = sb.last_invoice_url
        return Response(payload, status=status.HTTP_201_CREATED)


class RefundBillingView(APIView):
    """
    POST /api/payments/students/{student_id}/billing/refund/

    Estorno total (sem `value_cents`) ou parcial. Estorno total reverte o
    split automaticamente — a FichaGym devolve a fee também.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request, student_id: int):
        student = get_object_or_404(
            User, pk=student_id, role=User.Role.STUDENT, created_by=request.user
        )
        sb = get_object_or_404(StudentBilling, student=student)
        if not sb.asaas_payment_id:
            return Response(
                {"detail": "Sem pagamento concretizado pra estornar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        body = RefundSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        value_cents = body.validated_data.get("value_cents")
        value_reais = (
            round(value_cents / 100.0, 2) if value_cents else None
        )
        asaas_gateway.ensure_enabled()
        try:
            asaas_gateway.refund_payment(
                sb.asaas_payment_id, value_reais=value_reais
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        sb.status = StudentBilling.Status.REFUNDED
        sb.save(update_fields=["status", "updated_at"])
        return Response({"detail": "Estorno solicitado ao Asaas."})


class MyBillingView(APIView):
    """
    GET /api/payments/me/billing/ — o aluno vê quanto/como paga.

    Sem detalhes do trainer pra evitar expor info irrelevante; só a UX
    mínima pro aluno saber onde pagar.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        sb = StudentBilling.objects.filter(student=request.user).first()
        if not sb:
            return Response({"exists": False})
        payload = StudentBillingSerializer(sb).data
        payload["exists"] = True
        return Response(payload)


# ---------------------------------------------------------------------------
# Webhook do split (cobranças de aluno)
# ---------------------------------------------------------------------------
class ConnectWebhookView(APIView):
    """
    POST /api/payments/webhook/connect/

    Eventos de cobranças do split. Usa o MESMO token do `ASAAS_WEBHOOK_TOKEN`
    (config de webhook é única por conta master no Asaas). Se quiser separar
    do webhook do billing/, mover pro mesmo endpoint e despachar por
    `externalReference` (que carrega `sb_<id>` ou `user_<id>`).
    """

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        expected = getattr(settings, "ASAAS_WEBHOOK_TOKEN", "")
        if not expected:
            return Response(
                {"detail": "Webhook não configurado."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
        obj = payload.get("payment") or payload.get("subscription") or {}

        try:
            sb = _resolve_billing(obj)
            if sb is None:
                log.info("Webhook split %s sem StudentBilling — ignorando", event)
            elif event in {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}:
                sb.status = StudentBilling.Status.ACTIVE
                sb.asaas_payment_id = obj.get("id") or sb.asaas_payment_id
                period_end = _parse_iso(obj.get("dueDate"))
                if period_end:
                    sb.current_period_end = period_end
                sb.save()
            elif event == "PAYMENT_OVERDUE":
                sb.status = StudentBilling.Status.PAST_DUE
                sb.save(update_fields=["status", "updated_at"])
            elif event == "PAYMENT_REFUNDED":
                sb.status = StudentBilling.Status.REFUNDED
                sb.save(update_fields=["status", "updated_at"])
            elif event == "SUBSCRIPTION_DELETED":
                sb.status = StudentBilling.Status.CANCELED
                sb.save(update_fields=["status", "updated_at"])
        except Exception:
            log.exception("Erro processando webhook split %s", event)

        return Response(status=status.HTTP_200_OK)


def _resolve_billing(obj: dict) -> StudentBilling | None:
    ext = obj.get("externalReference") or ""
    if ext.startswith("sb_"):
        try:
            sb_id = int(ext.split("_", 1)[1])
        except ValueError:
            return None
        return StudentBilling.objects.filter(pk=sb_id).first()
    sub_id = obj.get("subscription")
    if sub_id:
        sb = StudentBilling.objects.filter(asaas_subscription_id=sub_id).first()
        if sb:
            return sb
    customer = obj.get("customer")
    if customer:
        return StudentBilling.objects.filter(asaas_customer_id=customer).first()
    return None
