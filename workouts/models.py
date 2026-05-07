"""
Modelos do domínio de treino.

Estrutura:
    - Exercise: catálogo global de exercícios (público + privados por trainer).
                Carrega o "DNA" do exercício — nome, grupo muscular, GIF, dica padrão.
    - Workout:  ficha de treino do aluno, criada por um trainer.
    - WorkoutExercise: instância de um Exercise dentro de uma Workout, com os
                       parâmetros específicos da ficha (séries, reps, carga, descanso).

Quando o trainer monta uma ficha, ele SELECIONA um Exercise do catálogo (em vez
de digitar nome/grupo do zero) e parametriza as séries/reps específicas.
"""

import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models

from .validators import exercise_demo_upload_path, validate_demo_gif_size


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
class Exercise(models.Model):
    """
    Catálogo de exercícios.

    Visibilidade:
        - is_public=True  → todos os trainers veem.
        - is_public=False → só o trainer `created_by` (e admins) vê.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField("Nome", max_length=120)
    muscle_group = models.CharField("Grupo muscular", max_length=80)

    default_technique_note = models.TextField(
        "Dica técnica padrão", blank=True,
        help_text="Aparece quando uma ficha não sobrescreve com sua própria dica.",
    )
    demo_gif = models.FileField(
        "GIF demonstrativo",
        upload_to=exercise_demo_upload_path,
        null=True, blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["gif"]),
            validate_demo_gif_size,
        ],
        help_text="GIF demonstrativo do exercício (até 20 MB).",
    )

    is_public = models.BooleanField(
        "Público?", default=False, db_index=True,
        help_text="Se marcado, este exercício aparece pra todos os trainers.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="exercises_created",
        verbose_name="Criado por",
    )

    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["muscle_group"]),
            models.Index(fields=["is_public"]),
            models.Index(fields=["created_by"]),
        ]
        verbose_name = "Exercício (catálogo)"
        verbose_name_plural = "Exercícios (catálogo)"

    def __str__(self) -> str:
        prefix = "🌐" if self.is_public else "🔒"
        return f"{prefix} {self.name}"

    def is_visible_to(self, user) -> bool:
        """Determina se um user pode usar este exercício em fichas."""
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser:
            return True
        if self.is_public:
            return True
        return self.created_by_id == user.id


# ---------------------------------------------------------------------------
# Ficha de treino
# ---------------------------------------------------------------------------
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
        null=True, blank=True,
        verbose_name="Personal trainer",
    )

    name = models.CharField("Nome", max_length=100)
    focus = models.CharField("Foco", max_length=120)
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


# ---------------------------------------------------------------------------
# Exercício DENTRO de uma ficha (instância parametrizada)
# ---------------------------------------------------------------------------
class WorkoutExercise(models.Model):
    """
    Instância de um Exercise (catálogo) numa Workout (ficha), com os parâmetros
    específicos: séries, reps, carga sugerida, descanso, ordem.

    Ao deletar um Exercise do catálogo que está em uso, o Django bloqueia
    (PROTECT) — o trainer precisa remover dos workouts antes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workout = models.ForeignKey(
        Workout,
        on_delete=models.CASCADE,
        related_name="workout_exercises",
        verbose_name="Ficha",
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.PROTECT,
        related_name="usages",
        verbose_name="Exercício do catálogo",
    )

    order = models.PositiveSmallIntegerField("Ordem", default=0)

    sets = models.PositiveSmallIntegerField(
        "Séries", validators=[MinValueValidator(1)]
    )
    reps = models.CharField(
        "Repetições", max_length=20,
        help_text='Ex.: "8-12", "12" ou "AMRAP".',
    )
    load_kg = models.DecimalField(
        "Carga sugerida (kg)",
        max_digits=5, decimal_places=2,
        null=True, blank=True,
    )
    rest_seconds = models.PositiveSmallIntegerField("Descanso (s)", default=60)

    technique_note = models.TextField(
        "Dica específica desta ficha", blank=True,
        help_text="Sobrescreve a dica padrão do catálogo. Vazio = usa a padrão.",
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["workout", "order"],
                name="workoutexercise_unique_order_per_workout",
            ),
        ]
        indexes = [
            models.Index(fields=["workout"]),
            models.Index(fields=["exercise"]),
        ]
        verbose_name = "Exercício na ficha"
        verbose_name_plural = "Exercícios nas fichas"

    def __str__(self) -> str:
        return f"{self.exercise.name} ({self.sets}x{self.reps})"

    @property
    def effective_technique_note(self) -> str:
        """Dica desta instância, ou cai pro default do catálogo."""
        return self.technique_note or self.exercise.default_technique_note
