"""
Modelos do domínio de treino.

Espelha as structs Swift:
    - `Workout` em Models/Workout.swift
    - `Exercise` em Models/Workout.swift
"""

import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models

from .validators import exercise_demo_upload_path, validate_demo_gif_size


class Workout(models.Model):
    """
    Ficha de treino (ex.: "Treino A — Peito e Tríceps").
    Pertence a um aluno. Opcionalmente foi criada por um personal trainer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workouts_as_student",
        verbose_name="Aluno",
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="workouts_as_trainer",
        null=True,
        blank=True,
        verbose_name="Personal trainer",
    )

    name = models.CharField("Nome", max_length=100)            # "Treino A"
    focus = models.CharField("Foco", max_length=120)           # "Peito e Tríceps"
    day_label = models.CharField("Dia da semana", max_length=40)
    notes = models.TextField("Observações do personal", blank=True)

    created_at = models.DateTimeField("Criada em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizada em", auto_now=True)

    class Meta:
        ordering = ["day_label", "name"]
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["trainer"]),
        ]
        verbose_name = "Ficha de treino"
        verbose_name_plural = "Fichas de treino"

    def __str__(self) -> str:
        return f"{self.name} — {self.focus}"


class Exercise(models.Model):
    """
    Exercício dentro de uma ficha. Mantém ordem explícita (`order`)
    porque o Swift usa array — sem isso o app perderia a ordenação.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workout = models.ForeignKey(
        Workout,
        on_delete=models.CASCADE,
        related_name="exercises",
        verbose_name="Ficha",
    )
    order = models.PositiveSmallIntegerField("Ordem", default=0)

    name = models.CharField("Nome", max_length=120)
    muscle_group = models.CharField("Grupo muscular", max_length=80)
    sets = models.PositiveSmallIntegerField(
        "Séries", validators=[MinValueValidator(1)]
    )
    reps = models.CharField(
        "Repetições", max_length=20, help_text='Ex.: "8-12", "12" ou "AMRAP".'
    )
    load_kg = models.DecimalField(
        "Carga sugerida (kg)",
        max_digits=5, decimal_places=2,
        null=True, blank=True,
    )
    rest_seconds = models.PositiveSmallIntegerField("Descanso (s)", default=60)
    technique_note = models.TextField("Dica técnica", blank=True)
    # GIF demonstrativo enviado pelo trainer (não URL externa).
    # Validações: extensão .gif e tamanho até 20 MB.
    demo_gif = models.FileField(
        "GIF demonstrativo",
        upload_to=exercise_demo_upload_path,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["gif"]),
            validate_demo_gif_size,
        ],
        help_text="GIF demonstrativo do exercício (até 20 MB).",
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["workout", "order"],
                name="exercise_unique_order_per_workout",
            ),
        ]
        indexes = [models.Index(fields=["workout"])]
        verbose_name = "Exercício"
        verbose_name_plural = "Exercícios"

    def __str__(self) -> str:
        return f"{self.name} ({self.sets}x{self.reps})"
