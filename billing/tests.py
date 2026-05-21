"""Testes do app billing — signup grátis, estado da assinatura e webhook Asaas."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from .models import Subscription

User = get_user_model()


def _make_trainer(username="trainer1", **extra):
    extra.setdefault("email", f"{username}@example.com")
    return User.objects.create_user(
        username=username,
        password="x",
        role=User.Role.TRAINER,
        **extra,
    )


class TrainerSignupTests(TestCase):
    """POST /api/billing/signup/ — cadastro grátis (sem cartão)."""

    url = "/api/billing/signup/"

    def setUp(self):
        self.client = APIClient()
        self.payload = {
            "username": "novo_personal",
            "email": "novo@example.com",
            "password": "Senha-Forte-123!",
            "display_name": "Novo Personal",
            "phone": "+5511999990000",
        }

    def test_creates_trainer_and_returns_jwt(self):
        resp = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertEqual(resp.data["user"]["role"], User.Role.TRAINER)
        user = User.objects.get(username="novo_personal")
        self.assertEqual(user.role, User.Role.TRAINER)
        self.assertFalse(user.is_billing_exempt)

    def test_role_is_forced_trainer_even_if_client_lies(self):
        self.payload["role"] = User.Role.ADMIN
        resp = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="novo_personal")
        self.assertEqual(user.role, User.Role.TRAINER)

    def test_duplicate_username_returns_400(self):
        _make_trainer("novo_personal")
        resp = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", resp.data)

    def test_duplicate_email_case_insensitive_returns_400(self):
        _make_trainer("outro", email="NOVO@example.com")
        resp = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", resp.data)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
         "OPTIONS": {"min_length": 10}},
    ])
    def test_password_validator_is_applied_when_configured(self):
        self.payload["password"] = "curta"
        resp = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", resp.data)


class HasActiveSubscriptionTests(TestCase):
    """Property User.has_active_subscription — coração do paywall."""

    def test_false_when_no_subscription(self):
        trainer = _make_trainer()
        self.assertFalse(trainer.has_active_subscription)

    def test_true_when_subscription_active(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        self.assertTrue(trainer.has_active_subscription)

    def test_true_when_subscription_trialing(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.TRIALING,
        )
        self.assertTrue(trainer.has_active_subscription)

    def test_false_when_subscription_past_due(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.PAST_DUE,
        )
        self.assertFalse(trainer.has_active_subscription)

    def test_true_when_exempt_even_without_subscription(self):
        trainer = _make_trainer(is_billing_exempt=True)
        self.assertTrue(trainer.has_active_subscription)

    def test_true_when_superuser(self):
        trainer = _make_trainer()
        trainer.is_superuser = True
        trainer.save()
        self.assertTrue(trainer.has_active_subscription)

    def test_true_when_admin_role(self):
        admin = User.objects.create_user(
            username="adm", password="x", role=User.Role.ADMIN,
            email="adm@example.com",
        )
        self.assertTrue(admin.has_active_subscription)


class SubscriptionDetailViewTests(TestCase):
    """GET /api/billing/subscription/ — payload pro paywall do front."""

    url = "/api/billing/subscription/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    def test_reports_asaas_disabled_in_scaffold_mode(self):
        with override_settings(ASAAS_API_KEY=""):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["asaas_enabled"])
        self.assertFalse(resp.data["has_active_subscription"])
        self.assertIsNone(resp.data["subscription"])

    def test_reports_asaas_enabled_when_key_set(self):
        with override_settings(ASAAS_API_KEY="$aact_test_fake"):
            resp = self.client.get(self.url)
        self.assertTrue(resp.data["asaas_enabled"])

    def test_returns_subscription_snapshot_when_exists(self):
        Subscription.objects.create(
            user=self.trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.ANNUAL,
            status=Subscription.Status.ACTIVE,
            price_cents=30000,
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.data["subscription"]["status"], "active")
        self.assertEqual(resp.data["subscription"]["plan"], "annual")
        self.assertEqual(resp.data["subscription"]["price_cents"], 30000)
        self.assertTrue(resp.data["subscription"]["is_active_like"])


class ScaffoldEndpointTests(TestCase):
    """Endpoints que dependem do Asaas devem responder 503 sem chave."""

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    @override_settings(ASAAS_API_KEY="")
    def test_subscribe_returns_503_without_key(self):
        resp = self.client.post(
            "/api/billing/subscribe/", {"plan": "monthly"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ASAAS_API_KEY="")
    def test_cancel_returns_400_without_subscription(self):
        # Sem assinatura local nem chave — devolve 400 ("nada pra cancelar").
        resp = self.client.post("/api/billing/cancel/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(ASAAS_API_KEY="")
    def test_cancel_returns_503_when_has_subscription_but_no_key(self):
        Subscription.objects.create(
            user=self.trainer,
            asaas_customer_id="cus_test",
            asaas_subscription_id="sub_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        resp = self.client.post("/api/billing/cancel/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class SubscribeViewTests(TestCase):
    """POST /api/billing/subscribe/ — cria customer+subscription no Asaas."""

    url = "/api/billing/subscribe/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    @override_settings(
        ASAAS_API_KEY="$aact_test_fake",
        ASAAS_PRICES={"monthly": 4000, "annual": 30000},
    )
    def test_creates_customer_and_subscription_and_returns_invoice_url(self):
        # Mocka as 2 chamadas HTTP (customer + subscription) + a busca da
        # primeira fatura.
        def fake_request(method, path, *, json=None, params=None, api_key=None):
            if path == "/customers":
                return {"id": "cus_fake", "name": json["name"]}
            if path == "/subscriptions":
                return {
                    "id": "sub_fake",
                    "customer": "cus_fake",
                    "cycle": json["cycle"],
                    "value": json["value"],
                    "invoiceUrl": "https://sandbox.asaas.com/i/abc",
                }
            return {}

        with patch("billing.asaas_gateway.request", side_effect=fake_request):
            resp = self.client.post(self.url, {"plan": "monthly"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["url"], "https://sandbox.asaas.com/i/abc")
        self.assertEqual(resp.data["subscription_id"], "sub_fake")
        sub = Subscription.objects.get(user=self.trainer)
        self.assertEqual(sub.asaas_customer_id, "cus_fake")
        self.assertEqual(sub.asaas_subscription_id, "sub_fake")
        self.assertEqual(sub.price_cents, 4000)
        self.assertEqual(sub.last_invoice_url, "https://sandbox.asaas.com/i/abc")

    @override_settings(
        ASAAS_API_KEY="$aact_test_fake",
        ASAAS_PRICES={"monthly": 0, "annual": 0},
    )
    def test_returns_503_when_price_not_configured(self):
        resp = self.client.post(self.url, {"plan": "monthly"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class WebhookTests(TestCase):
    """POST /api/billing/webhook/ — autenticado por token compartilhado."""

    url = "/api/billing/webhook/"

    def setUp(self):
        self.client = APIClient()

    @override_settings(ASAAS_WEBHOOK_TOKEN="")
    def test_returns_503_without_token(self):
        resp = self.client.post(self.url, data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_returns_401_on_invalid_token(self):
        resp = self.client.post(
            self.url,
            data=b'{"event":"PAYMENT_CONFIRMED","payment":{}}',
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="wrong",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_returns_400_on_malformed_payload(self):
        resp = self.client.post(
            self.url,
            data=b"not-json",
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="secret-token",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_payment_confirmed_marks_active(self):
        trainer = _make_trainer()
        sub = Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            asaas_subscription_id="sub_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.INCOMPLETE,
        )
        body = json.dumps({
            "event": "PAYMENT_CONFIRMED",
            "payment": {
                "id": "pay_1",
                "subscription": "sub_test",
                "customer": "cus_test",
                "dueDate": "2026-06-20",
                "externalReference": f"user_{trainer.id}",
            },
        }).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="secret-token",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(sub.current_period_end)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_payment_overdue_marks_past_due(self):
        trainer = _make_trainer()
        sub = Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            asaas_subscription_id="sub_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        body = json.dumps({
            "event": "PAYMENT_OVERDUE",
            "payment": {
                "id": "pay_1",
                "subscription": "sub_test",
                "customer": "cus_test",
            },
        }).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="secret-token",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PAST_DUE)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_subscription_deleted_marks_canceled(self):
        trainer = _make_trainer()
        sub = Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            asaas_subscription_id="sub_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        body = json.dumps({
            "event": "SUBSCRIPTION_DELETED",
            "subscription": {"id": "sub_test", "customer": "cus_test"},
        }).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="secret-token",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELED)
        self.assertTrue(sub.cancel_at_period_end)

    @override_settings(ASAAS_WEBHOOK_TOKEN="secret-token")
    def test_unknown_event_returns_200_and_does_not_error(self):
        body = json.dumps({"event": "ACCOUNT_STATUS_UPDATED", "data": {}}).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_ASAAS_ACCESS_TOKEN="secret-token",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
