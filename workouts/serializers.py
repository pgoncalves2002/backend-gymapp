"""
Serializers do app workouts.

Estratégia:
    - List: sem exercises aninhados, mas com `exercises_count` e
      `estimated_duration_minutes` (replicando a heurística do app Swift).
    - Detail: traz `exercises` completos.
    - Escrita: `Workout` cria sem exercises; exercises são manipulados via
      seu próprio endpoint /api/exercises/.
"""

from rest_framework import serializers

from accounts.models import User

from .models import Exercise, Workout


# ---------------------------------------------------------------------------
# Exercise
# ---------------------------------------------------------------------------
class ExerciseSerializer(serializers.ModelSerializer):
    # FileField padrão do DRF: na leitura devolve URL absoluta (ex.:
    # http://localhost:8000/media/exercises/<wid>/<eid>.gif). Na escrita
    # aceita arquivo via multipart/form-data com a chave `demo_gif`.
    class Meta:
        model = Exercise
        fields = (
            "id",
            "workout",
            "order",
            "name",
            "muscle_group",
            "sets",
            "reps",
            "load_kg",
            "rest_seconds",
            "technique_note",
            "demo_gif",
        )
        read_only_fields = ("id",)


# ---------------------------------------------------------------------------
# Workout
# ---------------------------------------------------------------------------
class _WorkoutBaseSerializer(serializers.ModelSerializer):
    """Base com campos comuns de leitura."""

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
            "exercises_count",
            "estimated_duration_minutes",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "trainer", "created_at", "updated_at")

    def get_exercises_count(self, obj: Workout) -> int:
        # `exercises_count` vem anotado pelo ViewSet (annotate Count) quando possível;
        # cai pra .count() se a annotation não existir.
        cached = getattr(obj, "_exercises_count", None)
        if cached is not None:
            return cached
        return obj.exercises.count()

    def get_estimated_duration_minutes(self, obj: Workout) -> int:
        """
        Heurística idêntica à `Workout.estimatedDurationMinutes` do Swift:
            ~60s por série + tempo de descanso, mínimo 15 minutos.
        """
        exercises = list(obj.exercises.all())
        total_sets = sum(e.sets for e in exercises)
        rest_seconds = sum(e.sets * e.rest_seconds for e in exercises)
        total = (total_sets * 60) + rest_seconds
        return max(15, total // 60)


class WorkoutListSerializer(_WorkoutBaseSerializer):
    """Para `GET /api/workouts/` — sem o array completo de exercícios."""


class WorkoutDetailSerializer(_WorkoutBaseSerializer):
    """Para `GET /api/workouts/{id}/` — com `exercises` aninhado (read-only)."""

    exercises = ExerciseSerializer(many=True, read_only=True)

    class Meta(_WorkoutBaseSerializer.Meta):
        fields = _WorkoutBaseSerializer.Meta.fields + ("exercises",)
