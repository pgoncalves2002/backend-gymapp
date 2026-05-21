"""Testes do app payments (split — personal cobra aluno, FichaGym fica com %)."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from .models import ConnectedAccount, StudentBilling

User = get_user_model()


def _trainer(**extra):
    extra.setdefault("email", "t@example.com")
    extra.setdefault("uses_internal_payment", True)
    return User.objects.create_user(
        username="trainer", password="x", role=User.Role.TRAINER, **extra
    )


def _student_of(trainer, *, uses_internal=True, **extra):
    extra.setdefault("email", "s@example.com")
    return User.objects.create_user(
        username="student",
        password="x",
        role=User.Role.STUDENT,
        created_by=trainer,
        uses_internal_payment=uses_internal,
        **extra,
    )


class OnboardConnectTests(TestCase):
    """POST /api/payments/connect/onboard/."""

    url = "/api/payments/connect/onboard/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _trainer()
        self.client.force_authenticate(self.trainer)

    @override_settings(ASAAS_API_KEY="")
    def test_returns_503_in_scaffold_mode(self):
        resp = self.client.post(
            self.url,
            {
                "cpf_cnpj": "12345678900",
                "income_value": 3000.0,
                "postal_code": "01001000",
                "address": "Praça da Sé",
                "address_number": "100",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ASAAS_API_KEY="$aact_test_fake")
    def test_requires_uses_internal_payment(self):
        self.trainer.uses_internal_payment = False
        self.trainer.save()
        resp = self.client.post(
            self.url,
            {
                "cpf_cnpj": "12345678900",
                "income_value": 3000.0,
                "postal_code": "01001000",
                "address": "Praça da Sé",
                "address_number": "100",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(ASAAS_API_KEY="$aact_test_fake")
    def test_creates_subaccount_and_returns_status(self):
        def fake_request(method, path, *, json=None, params=None, api_key=None):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/accounts")
            return {
                "id": "acc_fake",
                "walletId": "wallet_fake",
                "apiKey": "$aact_subaccount_fake",
            }

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(
                self.url,
                {
                    "cpf_cnpj": "12345678900",
                    "income_value": 3000.0,
                    "postal_code": "01001000",
                    "address": "Praça da Sé",
                    "address_number": "100",
                },
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        ca = ConnectedAccount.objects.get(user=self.trainer)
        self.assertEqual(ca.asaas_account_id, "acc_fake")
        self.assertEqual(ca.wallet_id, "wallet_fake")
        self.assertTrue(ca.onboarding_completed)
        self.assertTrue(ca.is_ready)


class ConnectStatusTests(TestCase):
    """GET /api/payments/connect/status/."""

    url = "/api/payments/connect/status/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _trainer()
        self.client.force_authenticate(self.trainer)

    def test_reports_not_existing(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["exists"])
        self.assertFalse(resp.data["is_ready"])

    def test_reports_existing_account(self):
        ConnectedAccount.objects.create(
            user=self.trainer,
            asaas_account_id="acc_1",
            wallet_id="wallet_1",
            onboarding_completed=True,
            can_receive=True,
        )
        resp = self.client.get(self.url)
        self.assertTrue(resp.data["exists"])
        self.assertTrue(resp.data["is_ready"])


@override_settings(
    ASAAS_API_KEY="$aact_test_fake",
    PLATFORM_FEE_PERCENT=5.0,
)
class StudentBillingTests(TestCase):
    """POST /api/payments/students/{id}/billing/ — cria cobrança com split."""

    def setUp(self):
        self.client = APIClient()
        self.trainer = _trainer()
        self.student = _student_of(self.trainer)
        self.ca = ConnectedAccount.objects.create(
            user=self.trainer,
            asaas_account_id="acc_1",
            wallet_id="wallet_1",
            onboarding_completed=True,
            can_receive=True,
        )
        self.client.force_authenticate(self.trainer)
        self.url = f"/api/payments/students/{self.student.id}/billing/"

    def test_returns_400_when_student_not_internal(self):
        self.student.uses_internal_payment = False
        self.student.save()
        resp = self.client.post(
            self.url, {"price_cents": 15000, "mode": "recurring"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_403_when_trainer_not_internal(self):
        self.trainer.uses_internal_payment = False
        self.trainer.save()
        resp = self.client.post(
            self.url, {"price_cents": 15000, "mode": "recurring"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_403_when_connected_account_not_ready(self):
        self.ca.can_receive = False
        self.ca.save()
        resp = self.client.post(
            self.url, {"price_cents": 15000, "mode": "recurring"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_creates_subscription_with_split_for_recurring(self):
        captured = {}

        def fake_request(method, path, *, json=None, params=None, api_key=None):
            captured[path] = json
            if path == "/customers":
                return {"id": "cus_student"}
            if path == "/subscriptions":
                return {"id": "sub_student", "invoiceUrl": "https://inv/abc"}
            return {}

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(
                self.url, {"price_cents": 15000, "mode": "recurring"}, format="json"
            )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Split: 100 - 5 (fee) = 95% pro personal.
        self.assertEqual(
            captured["/subscriptions"]["split"],
            [{"walletId": "wallet_1", "percentualValue": 95.0}],
        )
        self.assertEqual(captured["/subscriptions"]["cycle"], "MONTHLY")
        self.assertEqual(captured["/subscriptions"]["value"], 150.0)

        sb = StudentBilling.objects.get(student=self.student)
        self.assertEqual(sb.trainer, self.trainer)
        self.assertEqual(sb.price_cents, 15000)
        self.assertEqual(sb.asaas_subscription_id, "sub_student")
        self.assertEqual(sb.last_invoice_url, "https://inv/abc")

    def test_creates_one_off_payment_with_split(self):
        def fake_request(method, path, *, json=None, params=None, api_key=None):
            if path == "/customers":
                return {"id": "cus_student"}
            if path == "/payments":
                return {"id": "pay_one", "invoiceUrl": "https://inv/oneoff"}
            return {}

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(
                self.url, {"price_cents": 5000, "mode": "one_off"}, format="json"
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sb = StudentBilling.objects.get(student=self.student)
        self.assertEqual(sb.mode, StudentBilling.Mode.ONE_OFF)
        self.assertEqual(sb.asaas_payment_id, "pay_one")


@override_settings(ASAAS_API_KEY="$aact_test_fake")
class RefundTests(TestCase):
    """POST /api/payments/students/{id}/billing/refund/."""

    def setUp(self):
        self.client = APIClient()
        self.trainer = _trainer()
        self.student = _student_of(self.trainer)
        self.sb = StudentBilling.objects.create(
            student=self.student,
            trainer=self.trainer,
            mode=StudentBilling.Mode.ONE_OFF,
            price_cents=10000,
            asaas_customer_id="cus_s",
            asaas_payment_id="pay_1",
            status=StudentBilling.Status.ACTIVE,
        )
        self.client.force_authenticate(self.trainer)
        self.url = f"/api/payments/students/{self.student.id}/billing/refund/"

    def test_full_refund(self):
        def fake_request(method, path, *, json=None, params=None, api_key=None):
            self.assertEqual(path, "/payments/pay_1/refund")
            self.assertIsNone(json)  # total
            return {"refunded": True}

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sb.refresh_from_db()
        self.assertEqual(self.sb.status, StudentBilling.Status.REFUNDED)

    def test_partial_refund_passes_value(self):
        def fake_request(method, path, *, json=None, params=None, api_key=None):
            self.assertEqual(path, "/payments/pay_1/refund")
            self.assertEqual(json, {"value": 50.0})
            return {"refunded": True}

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(self.url, {"value_cents": 5000}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class WebhookConnectTests(TestCase):
    """POST /api/payments/webhook/connect/."""

    url = "/api/payments/webhook/connect/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _trainer()
        self.student = _student_of(self.trainer)
        self.sb = StudentBilling.objects.create(
            student=self.student,
            trainer=self.trainer,
            mode=StudentBilling.Mode.RECURRING,
            price_cents=15000,
            asaas_customer_id="cus_s",
            asaas_subscription_id="sub_s",
            status=StudentBilling.Status.PENDING,
        )

    @override_settings(ASAAS_WEBHOOK_TOKEN="")
    def test_returns_503_without_token(self):
        resp = self.client.post(self.url, data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ASAAS_WEBHOOK_TOKEN="t")
    def test_returns_401_on_wrong_token(self):
        resp = self.client.post(
            self.url,
            data=b'{"event":"PAYMENT_CONFIRMED","payment":{}}',
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="wrong",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(ASAAS_WEBHOOK_TOKEN="t")
    def test_payment_confirmed_marks_active(self):
        body = json.dumps({
            "event": "PAYMENT_CONFIRMED",
            "payment": {
                "id": "pay_x",
                "subscription": "sub_s",
                "customer": "cus_s",
                "dueDate": "2026-06-20",
                "externalReference": f"sb_{self.sb.id}",
            },
        }).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="t",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sb.refresh_from_db()
        self.assertEqual(self.sb.status, StudentBilling.Status.ACTIVE)
        self.assertEqual(self.sb.asaas_payment_id, "pay_x")

    @override_settings(ASAAS_WEBHOOK_TOKEN="t")
    def test_payment_refunded_marks_refunded(self):
        body = json.dumps({
            "event": "PAYMENT_REFUNDED",
            "payment": {
                "id": "pay_x",
                "subscription": "sub_s",
                "customer": "cus_s",
            },
        }).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="t",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sb.refresh_from_db()
        self.assertEqual(self.sb.status, StudentBilling.Status.REFUNDED)


class MyBillingTests(TestCase):
    """GET /api/payments/me/billing/ — visão do aluno."""

    url = "/api/payments/me/billing/"

    def test_returns_exists_false_when_no_billing(self):
        student = User.objects.create_user(
            username="solo", password="x", role=User.Role.STUDENT, email="s@e.com"
        )
        c = APIClient()
        c.force_authenticate(student)
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["exists"])

    def test_returns_billing_state(self):
        trainer = _trainer()
        student = _student_of(trainer)
        StudentBilling.objects.create(
            student=student,
            trainer=trainer,
            mode=StudentBilling.Mode.RECURRING,
            price_cents=12000,
            asaas_customer_id="cus_s",
            asaas_subscription_id="sub_s",
            last_invoice_url="https://inv/x",
            status=StudentBilling.Status.ACTIVE,
        )
        c = APIClient()
        c.force_authenticate(student)
        resp = c.get(self.url)
        self.assertTrue(resp.data["exists"])
        self.assertEqual(resp.data["price_cents"], 12000)
        self.assertEqual(resp.data["last_invoice_url"], "https://inv/x")
        self.assertTrue(resp.data["is_active_like"])
