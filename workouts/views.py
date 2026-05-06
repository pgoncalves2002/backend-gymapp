"""
ViewSets do app workouts.

Regras de visibilidade:
    - Aluno vê apenas as fichas onde é `student`.
    - Trainer vê apenas as fichas onde é `trainer`.
    - Trainer cria fichas (escolhe o aluno) e adiciona/edita exercícios.
    - Aluno não cria nem edita ficha (só consume).
"""

from django.db.models import Count, Prefetch
from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied

from accounts.permissions import IsTrainer, IsWorkoutOwnerOrTrainer

from .models import Exercise, Workout
from .serializers import (
    ExerciseSerializer,
    WorkoutDetailSerializer,
    WorkoutListSerializer,
)


class WorkoutViewSet(viewsets.ModelViewSet):
    """
    /api/workouts/         — list/create
    /api/workouts/{id}/    — retrieve/update/partial_update/destroy
    """

    permission_classes = (permissions.IsAuthenticated, IsWorkoutOwnerOrTrainer)

    def get_queryset(self):
        user = self.request.user
        qs = (
            Workout.objects
            .select_related("student", "trainer")
            .prefetch_related(Prefetch("exercises", queryset=Exercise.objects.order_by("order")))
            .annotate(_exercises_count=Count("exercises"))
        )
        if user.is_trainer:
            return qs.filter(trainer=user)
        # student
        return qs.filter(student=user)

    def get_serializer_class(self):
        if self.action == "list":
            return WorkoutListSerializer
        return WorkoutDetailSerializer

    def get_permissions(self):
        # Só trainer cria/edita/deleta. Aluno apenas lê (list/retrieve).
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [permissions.IsAuthenticated(), IsTrainer(), IsWorkoutOwnerOrTrainer()]
        return [permissions.IsAuthenticated(), IsWorkoutOwnerOrTrainer()]

    def perform_create(self, serializer):
        # `trainer` sempre é quem está autenticado.
        serializer.save(trainer=self.request.user)


class ExerciseViewSet(viewsets.ModelViewSet):
    """
    /api/exercises/        — list/create
    /api/exercises/{id}/   — retrieve/update/partial_update/destroy

    Filtro: o aluno vê os exercícios das suas fichas; o trainer vê das fichas
    que criou. Escrita só pelo trainer dono da ficha.
    """

    serializer_class = ExerciseSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        qs = Exercise.objects.select_related("workout").order_by("workout_id", "order")
        if user.is_trainer:
            return qs.filter(workout__trainer=user)
        return qs.filter(workout__student=user)

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [permissions.IsAuthenticated(), IsTrainer()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        workout = serializer.validated_data["workout"]
        if workout.trainer_id != self.request.user.id:
            raise PermissionDenied("Você não é o trainer dessa ficha.")
        serializer.save()

    def perform_update(self, serializer):
        # Reaplica a checagem caso o `workout` seja trocado no PATCH.
        workout = serializer.validated_data.get("workout") or serializer.instance.workout
        if workout.trainer_id != self.request.user.id:
            raise PermissionDenied("Você não é o trainer dessa ficha.")
        serializer.save()
