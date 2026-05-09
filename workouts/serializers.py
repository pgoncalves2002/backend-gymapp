"""
Serializers do app workouts.

Estrutura:
    - ExerciseSerializer: catálogo (read/write).
    - WorkoutExerciseSerializer: item de ficha — `exercise` aninhado read-only,
      mas escrita aceita `exercise` como PK (selecionar do catálogo).
    - WorkoutListSerializer / WorkoutDetailSerializer: ficha com agregados
      (exercises_count, estimated_duration_minutes) e expansão completa.
"""

from rest_framework import serializers

from accounts.models import User

from .models import Exercise, SetPreset, Workout, WorkoutExercise


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
class ExerciseSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Exercise
        fields = (
            "id",
            "name",
            "muscle_group",
            "default_technique_note",
            "demo_gif",
            "is_public",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")


# ---------------------------------------------------------------------------
# Item da ficha
# ---------------------------------------------------------------------------
class WorkoutExerciseSerializer(serializers.ModelSerializer):
    """
    Leitura: `exercise_detail` traz o catálogo aninhado (com nome, gif, etc.).
    Escrita: `exercise` é a PK do Exercise no catálogo.
    """

    exercise_detail = ExerciseSerializer(source="exercise", read_only=True)

    class Meta:
        model = WorkoutExercise
        fields = (
            "id",
            "workout",
            "exercise",         # PK na escrita
            "exercise_detail",  # objeto na leitura
            "order",
            "sets",
            "reps",
            "load_kg",
            "rest_seconds",
            "technique_note",
        )
        read_only_fields = ("id", "exercise_detail")


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------
class _WorkoutBaseSerializer(serializers.ModelSerializer):
    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.STUDENT)
    )
    trainer = serializers.PrimaryKeyRelatedField(read_only=True)
    exercises_count = serializers.SerializerMethodField()
    estimated_duration_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Workout
        fields = (
            "id",
            "student",
            "trainer",
            "name",
            "focus",
            "day_label",
            "notes",
            "is_archived",
            "exercises_count",
            "estimated_duration_minutes",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "trainer", "created_at", "updated_at")

    def get_exercises_count(self, obj: Workout) -> int:
        cached = getattr(obj, "_exercises_count", None)
        if cached is not None:
            return cached
        return obj.workout_exercises.count()

    def get_estimated_duration_minutes(self, obj: Workout) -> int:
        """
        Heurística idêntica à do app Swift:
            ~60s por série + tempo de descanso, mínimo 15 min.
        """
        items = list(obj.workout_exercises.all())
        total_sets = sum(it.sets for it in items)
        rest_seconds = sum(it.sets * it.rest_seconds for it in items)
        total = (total_sets * 60) + rest_seconds
        return max(15, total // 60)


class WorkoutListSerializer(_WorkoutBaseSerializer):
    """`GET /api/workouts/` — sem o array completo de exercícios."""


class WorkoutDetailSerializer(_WorkoutBaseSerializer):
    """`GET /api/workouts/{id}/` — com `workout_exercises` aninhado."""

    workout_exercises = WorkoutExerciseSerializer(many=True, read_only=True)

    class Meta(_WorkoutBaseSerializer.Meta):
        fields = _WorkoutBaseSerializer.Meta.fields + ("workout_exercises",)


# ---------------------------------------------------------------------------
# Preset de parâmetros de série
# ---------------------------------------------------------------------------
class SetPresetSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = SetPreset
        fields = (
            "id",
            "created_by",
            "name",
            "sets",
            "reps",
            "load_kg",
            "rest_seconds",
            "technique_note",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")
