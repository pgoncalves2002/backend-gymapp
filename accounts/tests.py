"""Testes do app accounts — validade do acesso do aluno (active_until)."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

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


class IsWithinValidityTests(TestCase):
    """User.is_within_validity — coração do bloqueio de acesso do aluno."""

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

    def test_admin_role_ignores_active_until(self):
        admin = User.objects.create_user(
            username="adm",
            password="x",
            role=User.Role.ADMIN,
            email="adm@example.com",
            active_until=date.today() - timedelta(days=365),
        )
        self.assertTrue(admin.is_within_validity)

    def test_superuser_ignores_active_until(self):
        self.student.is_superuser = True
        self.student.active_until = date.today() - timedelta(days=1)
        self.student.save(update_fields=["is_superuser", "active_until"])
        self.assertTrue(self.student.is_within_validity)


class SyncBlockedWhenExpiredTests(TestCase):
    """GET /api/sync/ retorna workouts=[] quando aluno expirado."""

    url = "/api/sync/"

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.student = _make_student_for(self.trainer, n=1)

    def test_active_student_gets_workouts(self):
        from workouts.models import Workout

        Workout.objects.create(
            student=self.student,
            trainer=self.trainer,
            name="Treino A",
            focus="Peito",
            day_label="seg",
        )
        self.client.force_authenticate(self.student)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["workouts"]), 1)
        self.assertTrue(resp.data["user"]["is_within_validity"])

    def test_expired_student_gets_empty_workouts(self):
        from workouts.models import Workout

        Workout.objects.create(
            student=self.student,
            trainer=self.trainer,
            name="Treino A",
            focus="Peito",
            day_label="seg",
        )
        self.student.active_until = date.today() - timedelta(days=1)
        self.student.save(update_fields=["active_until"])
        self.client.force_authenticate(self.student)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["workouts"], [])
        # Continua devolvendo o user pra o app montar tela de bloqueio.
        self.assertFalse(resp.data["user"]["is_within_validity"])


class StudentSerializerActiveUntilTests(TestCase):
    """PATCH /api/auth/students/{id}/ pra editar active_until."""

    def setUp(self):
        self.client = APIClient()
        self.trainer = _make_trainer()
        self.client.force_authenticate(self.trainer)

    def test_trainer_can_edit_active_until(self):
        s = _make_student_for(self.trainer, n=1)
        resp = self.client.patch(
            f"/api/auth/students/{s.id}/",
            {"active_until": "2027-01-01"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.active_until.isoformat(), "2027-01-01")

    def test_trainer_can_clear_active_until(self):
        s = _make_student_for(self.trainer, n=1)
        s.active_until = date(2027, 1, 1)
        s.save(update_fields=["active_until"])
        resp = self.client.patch(
            f"/api/auth/students/{s.id}/",
            {"active_until": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        s.refresh_from_db()
        self.assertIsNone(s.active_until)

    def test_blocks_edit_when_student_uses_internal_payment(self):
        # Prepara o cenário futuro: aluno com pagamento interno tem o
        # active_until automatizado, edição manual é rejeitada.
        self.trainer.uses_internal_payment = True
        self.trainer.save(update_fields=["uses_internal_payment"])
        s = _make_student_for(self.trainer, n=1)
        s.uses_internal_payment = True
        s.save(update_fields=["uses_internal_payment"])
        resp = self.client.patch(
            f"/api/auth/students/{s.id}/",
            {"active_until": "2027-01-01"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("active_until", resp.data)
