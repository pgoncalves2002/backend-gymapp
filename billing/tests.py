"""Testes do app billing — signup grátis, status da assinatura e webhook."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
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
        self.payload["role"] = User.Role.ADMIN  # tentativa de privilege escalation
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
            stripe_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        self.assertTrue(trainer.has_active_subscription)

    def test_true_when_subscription_trialing(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            stripe_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.TRIALING,
        )
        self.assertTrue(trainer.has_active_subscription)

    def test_false_when_subscription_past_due(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            stripe_customer_id="cus_test",
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

    def test_reports_stripe_disabled_in_scaffold_mode(self):
        with override_settings(STRIPE_SECRET_KEY=""):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["stripe_enabled"])
        self.assertFalse(resp.data["has_active_subscription"])
        self.assertIsNone(resp.data["subscription"])

    def test_reports_stripe_enabled_when_key_set(self):
        with override_settings(STRIPE_SECRET_KEY="sk_test_fake"):
            resp = self.client.get(self.url)
        self.assertTrue(resp.data["stripe_enabled"])

    def test_returns_subscription_snapshot_when_exists(self):
        Subscription.objects.create(
            user=self.trainer,
            stripe_customer_id="cus_test",
            plan=Subscription.Plan.ANNUAL,
            status=Subscription.Status.ACTIVE,
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.data["subscription"]["status"], "active")
        self.assertEqual(resp.data["subscription"]["plan"], "annual")
        self.assertTrue(resp.data["subscription"]["is_active_like"])


class ScaffoldEndpointTests(TestCase):
    """Endpoints que dependem da Stripe devem responder 503 sem chave."""

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_subscribe_returns_503_without_key(self):
        resp = self.client.post(
            "/api/billing/subscribe/", {"plan": "monthly"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_portal_returns_503_without_key(self):
        resp = self.client.post("/api/billing/portal/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class WebhookTests(TestCase):
    """POST /api/billing/webhook/ — assinado pela Stripe."""

    url = "/api/billing/webhook/"

    def setUp(self):
        self.client = APIClient()

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_returns_503_without_secret(self):
        resp = self.client.post(
            self.url, data=b"{}", content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_fake",
        STRIPE_WEBHOOK_SECRET="whsec_test_fake",
    )
    def test_returns_400_on_invalid_signature(self):
        resp = self.client.post(
            self.url,
            data=b'{"id":"evt_1","type":"invoice.paid","data":{"object":{}}}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_fake",
        STRIPE_WEBHOOK_SECRET="whsec_test_fake",
    )
    def test_returns_400_on_malformed_payload(self):
        resp = self.client.post(
            self.url,
            data=b"not-json",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_fake",
        STRIPE_WEBHOOK_SECRET="whsec_test_fake",
    )
    def test_syncs_subscription_on_valid_event(self):
        trainer = _make_trainer()
        sub = Subscription.objects.create(
            user=trainer,
            stripe_customer_id="cus_test",
            stripe_subscription_id="sub_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.INCOMPLETE,
        )
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_test",
                "customer": "cus_test",
                "status": "active",
                "cancel_at_period_end": False,
                "items": {"data": [{"price": {"id": "price_monthly"}}]},
            }},
        }
        with patch("stripe.Webhook.construct_event", return_value=event):
            resp = self.client.post(
                self.url,
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=whatever",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_fake",
        STRIPE_WEBHOOK_SECRET="whsec_test_fake",
    )
    def test_checkout_session_completed_creates_local_subscription(self):
        trainer = _make_trainer()
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "mode": "subscription",
                "customer": "cus_new",
                "subscription": "sub_new",
                "metadata": {"user_id": str(trainer.id), "plan": "annual"},
            }},
        }
        with patch("stripe.Webhook.construct_event", return_value=event):
            resp = self.client.post(
                self.url,
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=whatever",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        row = Subscription.objects.get(user=trainer)
        self.assertEqual(row.stripe_customer_id, "cus_new")
        self.assertEqual(row.stripe_subscription_id, "sub_new")
        self.assertEqual(row.status, Subscription.Status.ACTIVE)
        self.assertEqual(row.plan, "annual")

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_fake",
        STRIPE_WEBHOOK_SECRET="whsec_test_fake",
    )
    def test_checkout_session_completed_ignored_when_not_subscription(self):
        trainer = _make_trainer()
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "mode": "payment",  # one-off, não nos interessa
                "metadata": {"user_id": str(trainer.id)},
            }},
        }
        with patch("stripe.Webhook.construct_event", return_value=event):
            resp = self.client.post(
                self.url, data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=whatever",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Subscription.objects.filter(user=trainer).exists())
