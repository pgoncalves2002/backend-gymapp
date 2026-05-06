"""
Histórico de execuções de treino.

O struct `SetEntry` do Swift é runtime e some ao sair da tela —
estas tabelas tornam esse progresso persistente, possibilitando histórico,
estatísticas e progressão de carga.
"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from workouts.models import Exercise, Workout


class WorkoutSession(models.Model):
    """Uma execução de uma ficha pelo aluno."""

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "Em andamento"
        COMPLETED = "completed", "Concluído"
        ABANDONED = "abandoned", "Abandonado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workout = models.ForeignKey(
        Workout,
        on_delete=models.CASCADE,
        related_name="sessions",
        verbose_name="Ficha",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workout_sessions",
        verbose_name="Aluno",
    )

    started_at = models.DateTimeField("Iniciada em", auto_now_add=True)
    finished_at = models.DateTimeField("Finalizada em", null=True, blank=True)
    # corresponde ao workoutElapsedSeconds do app Swift
    elapsed_seconds = models.IntegerField("Tempo total (s)", default=0)
    status = models.CharField(
        "Status",
        max_length=15, choices=Status.choices, default=Status.IN_PROGRESS,
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["workout"]),
            models.Index(fields=["-started_at"]),
        ]
        verbose_name = "Sessão de treino"
        verbose_name_plural = "Sessões de treino"

    def __str__(self) -> str:
        return f"{self.workout.name} — {self.started_at:%d/%m/%Y %H:%M}"


class ExerciseSetLog(models.Model):
    """
    Cada série de cada exercício de uma sessão.
    Equivalente persistente do struct `SetEntry` do Swift.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        WorkoutSession,
        on_delete=models.CASCADE,
        related_name="set_logs",
        verbose_name="Sessão",
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.CASCADE,
        related_name="set_logs",
        verbose_name="Exercício",
    )
    set_number = models.PositiveSmallIntegerField(
        "Número da série", validators=[MinValueValidator(1)]
    )
    load_kg = models.DecimalField("Carga real (kg)", max_digits=5, decimal_places=2)
    is_completed = models.BooleanField("Concluída", default=False)
    completed_at = models.DateTimeField("Concluída em", null=True, blank=True)

    class Meta:
        ordering = ["exercise__order", "set_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "exercise", "set_number"],
                name="setlog_unique_per_session_exercise",
            ),
        ]
        indexes = [
            models.Index(fields=["session"]),
            models.Index(fields=["exercise"]),
        ]
        verbose_name = "Série executada"
        verbose_name_plural = "Séries executadas"

    def __str__(self) -> str:
        status = "✓" if self.is_completed else "○"
        return f"{status} série {self.set_number} — {self.load_kg}kg"
