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
    OneOffChargeSerializer,
    RefundSerializer,
    StudentBillingSerializer,
    TransferSerializer,
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
            income_value=data["income_value"],
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

        # Se já existe subscription do aluno (mesmo cancelada/estornada),
        # cancela no Asaas pra não ficar com duas ativas no provedor. O Asaas
        # devolve erro se já estiver cancelada — ignoramos.
        if sb.asaas_subscription_id:
            try:
                asaas_gateway.cancel_subscription(sb.asaas_subscription_id)
            except asaas_gateway.AsaasError as exc:
                log.info(
                    "Cancelamento da subscription antiga %s ignorado: %s",
                    sb.asaas_subscription_id,
                    exc.detail,
                )
            sb.asaas_subscription_id = ""

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
            sb.asaas_payment_id = ""  # limpa ponteiro pra payment antigo
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
        # Cobrança nova reinicia o ciclo — qualquer estado anterior (canceled,
        # refunded, past_due) vira pending até o aluno pagar.
        sb.status = StudentBilling.Status.PENDING
        sb.save()

        payload = StudentBillingSerializer(sb).data
        payload["url"] = sb.last_invoice_url
        return Response(payload, status=status.HTTP_201_CREATED)


class SyncStudentBillingView(APIView):
    """
    POST /api/payments/students/{student_id}/billing/sync/

    Força um GET na subscription/payments do Asaas e atualiza o status local.
    Útil em dev (sem túnel de webhook) e como fallback se um webhook se
    perder. Idempotente.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request, student_id: int):
        student = get_object_or_404(
            User, pk=student_id, role=User.Role.STUDENT, created_by=request.user
        )
        sb = get_object_or_404(StudentBilling, student=student)
        asaas_gateway.ensure_enabled()

        if sb.asaas_subscription_id:
            try:
                asaas_sub = asaas_gateway.get_subscription(sb.asaas_subscription_id)
                pays = asaas_gateway.request(
                    "GET", f"/subscriptions/{sb.asaas_subscription_id}/payments"
                )
            except asaas_gateway.AsaasError as exc:
                return Response({"detail": str(exc.detail)}, status=exc.status_code)
            asaas_status = (asaas_sub.get("status") or "").upper()
            # Cobrança mais recente é a que dita o estado atual — uma sub
            # ACTIVE com payment REFUNDED tem que aparecer como REFUNDED, não
            # active, senão a UI continua mostrando "Pagar agora" pra uma
            # invoiceUrl que o Asaas já apagou (404).
            payments_list = pays.get("data") or []
            latest = payments_list[0] if payments_list else None
            new_status = sb.status
            if latest:
                ps = (latest.get("status") or "").upper()
                if ps in {"CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"}:
                    new_status = StudentBilling.Status.ACTIVE
                elif ps == "OVERDUE":
                    new_status = StudentBilling.Status.PAST_DUE
                elif ps == "REFUNDED":
                    new_status = StudentBilling.Status.REFUNDED
                elif ps == "PENDING":
                    new_status = StudentBilling.Status.PENDING
                sb.asaas_payment_id = latest.get("id") or sb.asaas_payment_id
                # Atualiza invoiceUrl pra apontar pra fatura mais recente
                # (cobranças estornadas perdem a URL no Asaas).
                if latest.get("invoiceUrl"):
                    sb.last_invoice_url = latest["invoiceUrl"]
            if asaas_status == "INACTIVE":
                new_status = StudentBilling.Status.CANCELED
            sb.status = new_status
            next_due = _parse_iso(asaas_sub.get("nextDueDate"))
            if next_due:
                sb.current_period_end = next_due
        elif sb.asaas_payment_id:
            try:
                p = asaas_gateway.request("GET", f"/payments/{sb.asaas_payment_id}")
            except asaas_gateway.AsaasError as exc:
                return Response({"detail": str(exc.detail)}, status=exc.status_code)
            ps = (p.get("status") or "").upper()
            if ps in {"CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"}:
                sb.status = StudentBilling.Status.ACTIVE
            elif ps == "OVERDUE":
                sb.status = StudentBilling.Status.PAST_DUE
            elif ps == "REFUNDED":
                sb.status = StudentBilling.Status.REFUNDED

        sb.save()
        return Response(StudentBillingSerializer(sb).data)


class StudentChargesView(APIView):
    """
    /api/payments/students/{student_id}/charges/

    POST: cria UMA cobrança avulsa extra (não substitui a mensalidade
          recorrente). Boa pra matrícula, aula extra, multa etc.
    GET:  lista as cobranças avulsas já geradas (filtra pela
          externalReference `extra_<student_id>_*`).
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def _student(self, request, student_id: int) -> User:
        return get_object_or_404(
            User, pk=student_id, role=User.Role.STUDENT, created_by=request.user
        )

    def _ensure_customer(self, student: User, trainer: User, cpf_cnpj: str | None) -> str:
        """Reutiliza customer do StudentBilling se existe; senão cria um novo."""
        sb = StudentBilling.objects.filter(student=student).first()
        if sb and sb.asaas_customer_id:
            return sb.asaas_customer_id
        customer = asaas_gateway.create_customer(
            name=student.display_name or student.username,
            email=student.email or None,
            cpf_cnpj=cpf_cnpj,
            mobile_phone=student.phone or None,
            external_reference=f"student_{student.id}",
        )
        # Persiste o customer_id pra o próximo POST reaproveitar. Se ainda
        # não tem StudentBilling, criamos um stub PENDING (sem mensalidade
        # recorrente) só pra guardar o customer_id — mas isso atrapalha o
        # gate da mensalidade recorrente. Melhor: NÃO criar stub aqui, só
        # devolver o customer_id. O personal pode criar a mensalidade depois.
        if sb:
            sb.asaas_customer_id = customer["id"]
            sb.save(update_fields=["asaas_customer_id", "updated_at"])
        return customer["id"]

    def post(self, request, student_id: int):
        student = self._student(request, student_id)
        body = OneOffChargeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data

        if not student.uses_internal_payment:
            raise ValidationError(
                {"student": "Habilite 'Pagamento interno' no aluno antes de cobrar."}
            )
        trainer_ca = _ensure_can_receive(request.user)
        asaas_gateway.ensure_enabled()

        cpf_cnpj = (data.get("cpf_cnpj") or "").strip() or None
        customer_id = self._ensure_customer(student, request.user, cpf_cnpj)
        value_reais = round(data["value_cents"] / 100.0, 2)
        description = (
            data.get("description")
            or f"Cobrança avulsa — {request.user.display_name or request.user.username}"
        )
        due_in = data.get("due_in_days") or 3
        # epoch curto pra distinguir cobranças avulsas múltiplas pro mesmo aluno
        import time
        ext_ref = f"extra_{student.id}_{int(time.time())}"

        payment = asaas_gateway.create_payment(
            customer_id=customer_id,
            value_reais=value_reais,
            due_date=_next_due_iso(due_in),
            billing_type="UNDEFINED",
            description=description,
            external_reference=ext_ref,
            split=_split_for(trainer_ca),
        )
        return Response(
            {
                "id": payment.get("id"),
                "url": payment.get("invoiceUrl"),
                "value_cents": int(round(float(payment.get("value") or 0) * 100)),
                "status": payment.get("status"),
                "billing_type": payment.get("billingType"),
                "due_date": payment.get("dueDate"),
                "description": payment.get("description"),
                "external_reference": payment.get("externalReference"),
            },
            status=status.HTTP_201_CREATED,
        )

    def get(self, request, student_id: int):
        student = self._student(request, student_id)
        sb = StudentBilling.objects.filter(student=student).first()
        if not sb or not sb.asaas_customer_id:
            return Response({"items": []})
        asaas_gateway.ensure_enabled()
        try:
            data = asaas_gateway.request(
                "GET",
                "/payments",
                params={"customer": sb.asaas_customer_id, "limit": 100},
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        items = []
        prefix = f"extra_{student.id}_"
        for p in data.get("data") or []:
            ext = (p.get("externalReference") or "")
            if not ext.startswith(prefix):
                continue
            items.append({
                "id": p.get("id"),
                "value_cents": int(round(float(p.get("value") or 0) * 100)),
                "status": p.get("status"),
                "billing_type": p.get("billingType"),
                "due_date": p.get("dueDate"),
                "payment_date": p.get("paymentDate") or p.get("confirmedDate"),
                "invoice_url": p.get("invoiceUrl"),
                "description": p.get("description"),
                "external_reference": ext,
            })
        return Response({"items": items})


class RefundChargeView(APIView):
    """
    POST /api/payments/students/{student_id}/charges/{charge_id}/refund/

    Estorno de uma cobrança avulsa específica. Total se sem body; parcial
    se passar `value_cents`.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request, student_id: int, charge_id: str):
        # Só permite estornar se o aluno foi criado pelo trainer logado.
        get_object_or_404(
            User, pk=student_id, role=User.Role.STUDENT, created_by=request.user
        )
        body = RefundSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        value_cents = body.validated_data.get("value_cents")
        value_reais = round(value_cents / 100.0, 2) if value_cents else None
        asaas_gateway.ensure_enabled()
        try:
            asaas_gateway.refund_payment(charge_id, value_reais=value_reais)
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        return Response({"detail": "Estorno solicitado ao Asaas."})


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


# ---------------------------------------------------------------------------
# Painel financeiro do personal (subconta Asaas)
# ---------------------------------------------------------------------------
def _require_ready_subaccount(user: User) -> ConnectedAccount:
    """Garante que o personal tem subconta pronta antes de operações financeiras."""
    ca: ConnectedAccount | None = getattr(user, "connected_account", None)
    if ca is None:
        raise PermissionDenied(
            "Cadastre sua conta de recebimento Asaas antes de acessar finanças."
        )
    if not ca.api_key_encrypted:
        raise PermissionDenied(
            "Subconta sem API key salva — recadastre a conta de recebimento."
        )
    return ca


def _guess_pix_key_type(key: str) -> str:
    """Detecção heurística — usuário pode sobrescrever no body."""
    digits = "".join(c for c in key if c.isdigit())
    if "@" in key:
        return "EMAIL"
    if len(digits) == 11 and key.startswith("+"):
        return "PHONE"
    if len(digits) == 11:
        return "CPF"
    if len(digits) == 14:
        return "CNPJ"
    return "EVP"  # chave aleatória (UUID)


class FinanceBalanceView(APIView):
    """GET /api/payments/me/finance/balance/ — saldo da subconta no Asaas."""

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request):
        ca = _require_ready_subaccount(request.user)
        asaas_gateway.ensure_enabled()
        try:
            data = asaas_gateway.get_balance(api_key=ca.api_key_encrypted)
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        # API devolve {"balance": <float>}; normaliza pra centavos pra evitar float drift.
        balance_reais = float(data.get("balance") or 0)
        return Response({
            "balance_cents": int(round(balance_reais * 100)),
            "balance_reais": balance_reais,
        })


class FinanceTransactionsView(APIView):
    """
    GET /api/payments/me/finance/transactions/?status=RECEIVED&limit=50

    Lista de cobranças do split (com a fee da FichaGym deduzida). Default
    traz RECEIVED + CONFIRMED + RECEIVED_IN_CASH.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request):
        ca = _require_ready_subaccount(request.user)
        asaas_gateway.ensure_enabled()
        try:
            limit = max(1, min(100, int(request.query_params.get("limit", 50))))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            limit, offset = 50, 0
        status_in_param = request.query_params.get("status")
        if status_in_param:
            status_in = [s.strip().upper() for s in status_in_param.split(",") if s.strip()]
        else:
            status_in = ["RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"]
        try:
            data = asaas_gateway.list_payments(
                status_in=status_in,
                limit=limit,
                offset=offset,
                api_key=ca.api_key_encrypted,
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        items = []
        for p in data.get("data") or []:
            items.append({
                "id": p.get("id"),
                "value_cents": int(round(float(p.get("value") or 0) * 100)),
                "net_value_cents": int(round(float(p.get("netValue") or 0) * 100)),
                "status": p.get("status"),
                "billing_type": p.get("billingType"),
                "due_date": p.get("dueDate"),
                "payment_date": p.get("paymentDate") or p.get("confirmedDate"),
                "customer": p.get("customer"),
                "description": p.get("description"),
                "external_reference": p.get("externalReference"),
            })
        return Response({
            "items": items,
            "total": data.get("totalCount"),
            "has_more": data.get("hasMore"),
        })


class FinanceTransferView(APIView):
    """
    POST /api/payments/me/finance/transfer/

    Body: {value_cents, pix_key, pix_key_type?, description?}

    Solicita um saque via Pix da subconta pra a chave Pix do recebedor.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def post(self, request):
        ca = _require_ready_subaccount(request.user)
        asaas_gateway.ensure_enabled()
        body = TransferSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        value_reais = round(data["value_cents"] / 100.0, 2)
        pix_key = data["pix_key"].strip()
        pix_key_type = (data.get("pix_key_type") or "").strip() or _guess_pix_key_type(pix_key)
        try:
            resp = asaas_gateway.create_transfer(
                value_reais=value_reais,
                pix_address_key=pix_key,
                pix_address_key_type=pix_key_type,
                description=data.get("description") or None,
                api_key=ca.api_key_encrypted,
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        return Response({
            "id": resp.get("id"),
            "status": resp.get("status"),
            "value_cents": int(round(float(resp.get("value") or 0) * 100)),
            "net_value_cents": int(round(float(resp.get("netValue") or 0) * 100)),
            "scheduled_date": resp.get("scheduledDate"),
            "type": resp.get("type"),
        }, status=status.HTTP_201_CREATED)


class FinanceTransferListView(APIView):
    """GET /api/payments/me/finance/transfers/ — histórico de saques."""

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request):
        ca = _require_ready_subaccount(request.user)
        asaas_gateway.ensure_enabled()
        try:
            data = asaas_gateway.list_transfers(api_key=ca.api_key_encrypted)
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        items = []
        for t in data.get("data") or []:
            items.append({
                "id": t.get("id"),
                "value_cents": int(round(float(t.get("value") or 0) * 100)),
                "net_value_cents": int(round(float(t.get("netValue") or 0) * 100)),
                "status": t.get("status"),
                "type": t.get("type"),
                "scheduled_date": t.get("scheduledDate"),
                "effective_date": t.get("effectiveDate"),
                "transfer_fee_cents": int(round(float(t.get("transferFee") or 0) * 100)),
            })
        return Response({
            "items": items,
            "total": data.get("totalCount"),
            "has_more": data.get("hasMore"),
        })


# ---------------------------------------------------------------------------
# Visão do aluno
# ---------------------------------------------------------------------------
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
        # Inclui o nome do trainer pra o aluno saber pra quem paga.
        payload["trainer_display_name"] = (
            sb.trainer.display_name or sb.trainer.username
        )
        return Response(payload)


class MyBillingCancelView(APIView):
    """
    POST /api/payments/me/billing/cancel/ — aluno cancela a própria assinatura.

    Cancela a subscription no Asaas (próximas cobranças param de ser geradas).
    O personal pode recriar depois se for o caso.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        sb = StudentBilling.objects.filter(student=request.user).first()
        if not sb or not sb.asaas_subscription_id:
            return Response(
                {"detail": "Você não tem assinatura ativa pra cancelar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        asaas_gateway.ensure_enabled()
        try:
            asaas_gateway.cancel_subscription(sb.asaas_subscription_id)
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        sb.status = StudentBilling.Status.CANCELED
        sb.save(update_fields=["status", "updated_at"])
        return Response({"detail": "Assinatura cancelada."})


class MyTransactionsView(APIView):
    """
    GET /api/payments/me/transactions/

    Histórico de pagamentos do aluno (todas as cobranças, pagas ou não).
    Consulta direto a master Asaas filtrando por customer.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        sb = StudentBilling.objects.filter(student=request.user).first()
        if not sb or not sb.asaas_customer_id:
            return Response({"items": [], "total": 0, "has_more": False})
        asaas_gateway.ensure_enabled()
        try:
            data = asaas_gateway.request(
                "GET",
                "/payments",
                params={"customer": sb.asaas_customer_id, "limit": 50},
            )
        except asaas_gateway.AsaasError as exc:
            return Response({"detail": str(exc.detail)}, status=exc.status_code)
        items = []
        for p in data.get("data") or []:
            items.append({
                "id": p.get("id"),
                "value_cents": int(round(float(p.get("value") or 0) * 100)),
                "status": p.get("status"),
                "billing_type": p.get("billingType"),
                "due_date": p.get("dueDate"),
                "payment_date": p.get("paymentDate") or p.get("confirmedDate"),
                "invoice_url": p.get("invoiceUrl"),
                "description": p.get("description"),
            })
        return Response({
            "items": items,
            "total": data.get("totalCount"),
            "has_more": data.get("hasMore"),
        })


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
