"""
Serializers do app training_sessions.

ExerciseSetLog agora referencia WorkoutExercise (instância na ficha).
"""

from django.utils import timezone
from rest_framework import serializers

from workouts.models import Workout

from .models import ExerciseSetLog, WorkoutSession


class ExerciseSetLogSerializer(serializers.ModelSerializer):
    """
    Atualizar com PATCH normalmente. Quando `is_completed` muda para True,
    `completed_at` é setado automaticamente (e zerado quando volta a False).
    """

    class Meta:
        model = ExerciseSetLog
        fields = (
            "id",
            "session",
            "workout_exercise",
            "set_number",
            "load_kg",
            "is_completed",
            "completed_at",
        )
        read_only_fields = (
            "id", "session", "workout_exercise", "set_number", "completed_at",
        )

    def update(self, instance: ExerciseSetLog, validated_data: dict) -> ExerciseSetLog:
        new_completed = validated_data.get("is_completed", instance.is_completed)
        if new_completed and not instance.is_completed:
            validated_data["completed_at"] = timezone.now()
        elif not new_completed and instance.is_completed:
            validated_data["completed_at"] = None
        return super().update(instance, validated_data)


class WorkoutSessionListSerializer(serializers.ModelSerializer):
    """Para `GET /api/sessions/` — histórico enxuto."""

    workout_name = serializers.CharField(source="workout.name", read_only=True)
    workout_focus = serializers.CharField(source="workout.focus", read_only=True)

    class Meta:
        model = WorkoutSession
        fields = (
            "id",
            "workout",
            "workout_name",
            "workout_focus",
            "started_at",
            "finished_at",
            "elapsed_seconds",
            "status",
        )
        read_only_fields = fields


class WorkoutSessionDetailSerializer(serializers.ModelSerializer):
    """Para `GET /api/sessions/{id}/` — com `set_logs` aninhados."""

    set_logs = ExerciseSetLogSerializer(many=True, read_only=True)
    workout_name = serializers.CharField(source="workout.name", read_only=True)

    class Meta:
        model = WorkoutSession
        fields = (
            "id",
            "workout",
            "workout_name",
            "student",
            "started_at",
            "finished_at",
            "elapsed_seconds",
            "status",
            "set_logs",
        )
        read_only_fields = ("id", "student", "started_at", "set_logs")


class WorkoutSessionCreateSerializer(serializers.ModelSerializer):
    """
    Para `POST /api/sessions/`. Recebe apenas `workout`; o backend:
        - valida que o workout pertence ao aluno autenticado;
        - cria a session com status=in_progress;
        - auto-cria ExerciseSetLog pra cada série de cada WorkoutExercise.
    """

    class Meta:
        model = WorkoutSession
        fields = ("id", "workout", "started_at", "status")
        read_only_fields = ("id", "started_at", "status")

    def validate_workout(self, workout: Workout) -> Workout:
        user = self.context["request"].user
        if workout.student_id != user.id:
            raise serializers.ValidationError("Esta ficha não pertence a você.")
        return workout

    def create(self, validated_data: dict) -> WorkoutSession:
        workout: Workout = validated_data["workout"]
        user = self.context["request"].user

        session = WorkoutSession.objects.create(
            workout=workout,
            student=user,
            status=WorkoutSession.Status.IN_PROGRESS,
        )

        # Pra cada item da ficha, cria N séries com carga sugerida.
        set_logs: list[ExerciseSetLog] = []
        for we in workout.workout_exercises.all():
            suggested = we.load_kg if we.load_kg is not None else 0
            for n in range(1, we.sets + 1):
                set_logs.append(
                    ExerciseSetLog(
                        session=session,
                        workout_exercise=we,
                        set_number=n,
                        load_kg=suggested,
                        is_completed=False,
                    )
                )
        ExerciseSetLog.objects.bulk_create(set_logs)
        return session


class WorkoutSessionUpdateSerializer(serializers.ModelSerializer):
    """
    Para `PATCH /api/sessions/{id}/`. Permite ajustar elapsed_seconds e status.
    Quando status vira `completed` ou `abandoned`, finished_at é setado.
    """

    class Meta:
        model = WorkoutSession
        fields = ("elapsed_seconds", "status", "finished_at")

    def update(self, instance: WorkoutSession, validated_data: dict) -> WorkoutSession:
        new_status = validated_data.get("status", instance.status)
        terminal = (WorkoutSession.Status.COMPLETED, WorkoutSession.Status.ABANDONED)
        if (
            new_status in terminal
            and instance.status not in terminal
            and "finished_at" not in validated_data
        ):
            validated_data["finished_at"] = timezone.now()
        return super().update(instance, validated_data)
