"""
ViewSets do app workouts.

Regras de visibilidade:
    - Aluno: vê apenas as fichas onde é `student`. Não vê o catálogo.
    - Trainer: vê suas fichas + catálogo (públicos + privados criados por ele).
    - Admin: vê tudo.
"""

from django.db.models import Count, Prefetch, Q
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.permissions import IsTrainer, IsWorkoutOwnerOrTrainer

from .models import Exercise, Workout, WorkoutExercise
from .serializers import (
    ExerciseSerializer,
    WorkoutDetailSerializer,
    WorkoutExerciseSerializer,
    WorkoutListSerializer,
)


# ---------------------------------------------------------------------------
# Catálogo: /api/exercises/
# ---------------------------------------------------------------------------
class ExerciseViewSet(viewsets.ModelViewSet):
    """
    /api/exercises/        — list/create
    /api/exercises/{id}/   — retrieve/update/partial_update/destroy

    Visibilidade:
        - Trainer: vê todos `is_public=True` + os criados por ele.
        - Admin/staff: vê tudo.
        - Aluno: 403 (não tem motivo de explorar o catálogo direto).
    """

    serializer_class = ExerciseSerializer
    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get_queryset(self):
        user = self.request.user
        qs = Exercise.objects.select_related("created_by")
        if not (user.is_staff or user.is_superuser):
            qs = qs.filter(Q(is_public=True) | Q(created_by=user)).distinct()

        # Filtro opcional por grupo muscular — usado pelo SPA do personal pra
        # alimentar o picker em cascata (Step 2 do editor de ficha).
        muscle_group = self.request.query_params.get("muscle_group")
        if muscle_group:
            qs = qs.filter(muscle_group__iexact=muscle_group.strip())

        return qs.order_by("muscle_group", "name")

    @action(detail=False, methods=["get"], url_path="muscle-groups")
    def muscle_groups(self, request):
        """
        GET /api/exercises/muscle-groups/

        Retorna a lista de grupos musculares distintos visíveis pro user
        logado, com contagem de exercícios em cada um. Usado pelo SPA do
        personal pra montar o Step 1 do picker (chips de grupos).

        Resposta:
            [{"name": "Peito", "count": 12}, {"name": "Costas", "count": 8}, ...]
        """
        # Não usamos `self.get_queryset()` direto porque ele aplica o filtro
        # `muscle_group=` (se viesse na query string), o que aqui não tem sentido —
        # queremos a lista completa visível pro user. Reaplica só o scoping.
        user = request.user
        base = Exercise.objects.all()
        if not (user.is_staff or user.is_superuser):
            base = base.filter(Q(is_public=True) | Q(created_by=user)).distinct()
        groups = (
            base.values("muscle_group")
            .annotate(count=Count("id"))
            .order_by("muscle_group")
        )
        return Response([
            {"name": g["muscle_group"], "count": g["count"]}
            for g in groups
            if g["muscle_group"]
        ])

    def perform_create(self, serializer):
        user = self.request.user
        extra = {"created_by": user}
        # Apenas superuser ou role=Admin podem marcar exercício como público.
        if not user.has_full_access:
            extra["is_public"] = False
        serializer.save(**extra)

    def perform_update(self, serializer):
        instance = serializer.instance
        user = self.request.user
        if not (user.has_full_access or instance.created_by_id == user.id):
            raise PermissionDenied("Só o criador (ou administrador) pode editar este exercício.")
        if not user.has_full_access and serializer.validated_data.get("is_public", False):
            raise PermissionDenied("Apenas administradores podem marcar exercícios como públicos.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.has_full_access or instance.created_by_id == user.id):
            raise PermissionDenied("Só o criador (ou administrador) pode remover este exercício.")
        instance.delete()


# ---------------------------------------------------------------------------
# Item da ficha: /api/workout-exercises/
# ---------------------------------------------------------------------------
class WorkoutExerciseViewSet(viewsets.ModelViewSet):
    """
    /api/workout-exercises/        — list/create
    /api/workout-exercises/{id}/   — retrieve/update/partial_update/destroy

    Quem pode mexer: só o trainer dono da ficha.
    Quem pode ler: trainer dono OU aluno dono (visualização).
    """

    serializer_class = WorkoutExerciseSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        qs = (
            WorkoutExercise.objects
            .select_related("workout", "exercise", "exercise__created_by")
            .order_by("workout_id", "order")
        )
        if user.is_trainer:
            return qs.filter(workout__trainer=user)
        return qs.filter(workout__student=user)

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [permissions.IsAuthenticated(), IsTrainer()]
        return [permissions.IsAuthenticated()]

    def _validate_workout_owner(self, workout):
        if workout.trainer_id != self.request.user.id:
            raise PermissionDenied("Você não é o trainer dessa ficha.")

    def _validate_exercise_visibility(self, exercise):
        if not exercise.is_visible_to(self.request.user):
            raise PermissionDenied(
                "Este exercício do catálogo não está disponível pra você."
            )

    def perform_create(self, serializer):
        self._validate_workout_owner(serializer.validated_data["workout"])
        self._validate_exercise_visibility(serializer.validated_data["exercise"])
        serializer.save()

    def perform_update(self, serializer):
        workout = serializer.validated_data.get("workout") or serializer.instance.workout
        self._validate_workout_owner(workout)
        exercise = serializer.validated_data.get("exercise") or serializer.instance.exercise
        self._validate_exercise_visibility(exercise)
        serializer.save()


# ---------------------------------------------------------------------------
# Ficha: /api/workouts/
# ---------------------------------------------------------------------------
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
            .prefetch_related(
                Prefetch(
                    "workout_exercises",
                    queryset=(
                        WorkoutExercise.objects
                        .select_related("exercise", "exercise__created_by")
                        .order_by("order")
                    ),
                )
            )
            .annotate(_exercises_count=Count("workout_exercises"))
        )
        if user.is_trainer:
            return qs.filter(trainer=user)
        return qs.filter(student=user)

    def get_serializer_class(self):
        if self.action == "list":
            return WorkoutListSerializer
        return WorkoutDetailSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [permissions.IsAuthenticated(), IsTrainer(), IsWorkoutOwnerOrTrainer()]
        return [permissions.IsAuthenticated(), IsWorkoutOwnerOrTrainer()]

    def perform_create(self, serializer):
        serializer.save(trainer=self.request.user)
