"""Testes do app accounts — gate freemium (limite de alunos do plano grátis)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from billing.models import Subscription

User = get_user_model()


def _make_trainer(username="trainer1", **extra):
    extra.setdefault("email", f"{username}@example.com")
    return User.objects.create_user(
        username=username,
        password="x",
        role=User.Role.TRAINER,
        **extra,
    )


def _make_student_for(trainer, n=1):
    return User.objects.create_user(
        username=f"aluno_{trainer.id}_{n}",
        password="x",
        role=User.Role.STUDENT,
        created_by=trainer,
        email=f"aluno_{trainer.id}_{n}@example.com",
    )


@override_settings(FREE_STUDENT_LIMIT=1)
class CanAddStudentModelTests(TestCase):
    """Regra do freemium na property User.can_add_student()."""

    def test_free_trainer_with_zero_students_can_add(self):
        trainer = _make_trainer()
        self.assertTrue(trainer.can_add_student())

    def test_free_trainer_at_limit_cannot_add(self):
        trainer = _make_trainer()
        _make_student_for(trainer)
        self.assertFalse(trainer.can_add_student())

    def test_exempt_trainer_ignores_limit(self):
        trainer = _make_trainer(is_billing_exempt=True)
        _make_student_for(trainer, n=1)
        _make_student_for(trainer, n=2)
        self.assertTrue(trainer.can_add_student())

    def test_subscribed_trainer_ignores_limit(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        _make_student_for(trainer)
        self.assertTrue(trainer.can_add_student())

    def test_past_due_subscription_does_not_bypass_limit(self):
        trainer = _make_trainer()
        Subscription.objects.create(
            user=trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.PAST_DUE,
        )
        _make_student_for(trainer)
        self.assertFalse(trainer.can_add_student())

    def test_superuser_ignores_limit(self):
        trainer = _make_trainer()
        trainer.is_superuser = True
        trainer.save()
        _make_student_for(trainer)
        self.assertTrue(trainer.can_add_student())

    def test_student_count_scopes_per_trainer(self):
        t1 = _make_trainer("t1")
        t2 = _make_trainer("t2")
        _make_student_for(t1, n=1)
        self.assertEqual(t1.student_count, 1)
        self.assertEqual(t2.student_count, 0)


@override_settings(FREE_STUDENT_LIMIT=1)
class StudentCreateGateAPITests(TestCase):
    """POST /api/auth/students/ — 402 quando passa do limite grátis."""

    url = "/api/auth/students/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    def _payload(self, n=1):
        return {
            "username": f"aluno_api_{n}",
            "email": f"aluno_api_{n}@example.com",
            "phone": "+5511988880001",
            "display_name": f"Aluno {n}",
        }

    def test_first_student_returns_201(self):
        resp = self.client.post(self.url, self._payload(1), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("temp_password", resp.data)

    def test_second_student_returns_402(self):
        self.client.post(self.url, self._payload(1), format="json")
        resp = self.client.post(self.url, self._payload(2), format="json")
        self.assertEqual(resp.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(self.trainer.student_count, 1)

    def test_exempt_trainer_passes_402_gate(self):
        self.trainer.is_billing_exempt = True
        self.trainer.save()
        self.client.post(self.url, self._payload(1), format="json")
        resp = self.client.post(self.url, self._payload(2), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_subscribed_trainer_passes_402_gate(self):
        Subscription.objects.create(
            user=self.trainer,
            asaas_customer_id="cus_test",
            plan=Subscription.Plan.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        self.client.post(self.url, self._payload(1), format="json")
        resp = self.client.post(self.url, self._payload(2), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
