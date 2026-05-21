"""Testes do app accounts — gate freemium (limite de alunos do plano grátis)."""

from datetime import date, timedelta

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


class StudentValidityTests(TestCase):
    """User.active_until + is_within_validity + bloqueio nas views do aluno."""

    def setUp(self):
        self.trainer = _make_trainer()
        self.student = _make_student_for(self.trainer, n=1)

    def test_no_active_until_means_within_validity(self):
        self.assertIsNone(self.student.active_until)
        self.assertTrue(self.student.is_within_validity)

    def test_active_until_in_past_blocks(self):
        self.student.active_until = date.today() - timedelta(days=1)
        self.student.save(update_fields=["active_until"])
        self.assertFalse(self.student.is_within_validity)

    def test_active_until_today_still_valid(self):
        self.student.active_until = date.today()
        self.student.save(update_fields=["active_until"])
        self.assertTrue(self.student.is_within_validity)

    def test_active_until_in_future_within_validity(self):
        self.student.active_until = date.today() + timedelta(days=30)
        self.student.save(update_fields=["active_until"])
        self.assertTrue(self.student.is_within_validity)

    def test_trainer_ignores_active_until(self):
        # active_until só vale pra aluno — trainer com data passada continua OK.
        self.trainer.active_until = date.today() - timedelta(days=10)
        self.trainer.save(update_fields=["active_until"])
        self.assertTrue(self.trainer.is_within_validity)

    def test_sync_returns_empty_workouts_when_expired(self):
        from workouts.models import Exercise, Workout
        ex = Exercise.objects.create(name="Supino", muscle_group="Peito", is_public=True)
        Workout.objects.create(
            student=self.student,
            trainer=self.trainer,
            name="Treino A",
            focus="Peito",
            day_label="seg",
        )
        self.student.active_until = date.today() - timedelta(days=1)
        self.student.save(update_fields=["active_until"])
        client = APIClient()
        client.force_authenticate(self.student)
        resp = client.get("/api/sync/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["workouts"], [])
        # Mas continua devolvendo o user (pro app mostrar a tela de bloqueio).
        self.assertFalse(resp.data["user"]["is_within_validity"])
        # Silenciar warning sobre Exercise não usado
        ex.delete()


class StudentSerializerActiveUntilValidationTests(TestCase):
    """Trainer só edita active_until quando aluno NÃO usa pagamento interno."""

    def setUp(self):
        self.trainer = _make_trainer()
        self.trainer.uses_internal_payment = True
        self.trainer.save()
        self.client = APIClient()
        self.client.force_authenticate(self.trainer)

    def test_can_edit_when_student_not_internal_payment(self):
        s = _make_student_for(self.trainer, n=1)
        url = f"/api/auth/students/{s.id}/"
        resp = self.client.patch(url, {"active_until": "2027-01-01"}, format="json")
        self.assertEqual(resp.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.active_until.isoformat(), "2027-01-01")

    def test_cannot_edit_when_student_uses_internal_payment(self):
        s = _make_student_for(self.trainer, n=1)
        s.uses_internal_payment = True
        s.save()
        url = f"/api/auth/students/{s.id}/"
        resp = self.client.patch(url, {"active_until": "2027-01-01"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("active_until", resp.data)
        s.refresh_from_db()
        self.assertIsNone(s.active_until)

    def test_can_clear_when_value_unchanged(self):
        # Mesmo com uses_internal_payment, mandar o valor ATUAL não dá 400.
        s = _make_student_for(self.trainer, n=1)
        s.uses_internal_payment = True
        s.active_until = date(2027, 1, 1)
        s.save()
        url = f"/api/auth/students/{s.id}/"
        resp = self.client.patch(
            url, {"active_until": "2027-01-01", "display_name": "Novo"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
