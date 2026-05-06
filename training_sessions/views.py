"""
ViewSets do app training_sessions.

Regras de visibilidade:
    - Aluno acessa apenas as próprias sessões.
    - Trainer não acessa sessões de execução (são privadas do aluno).
    - Aluno só pode atualizar séries (`load_kg`, `is_completed`) das próprias sessões.
"""

from rest_framework import permissions, viewsets

from accounts.permissions import IsSessionOwner

from .models import ExerciseSetLog, WorkoutSession
from .serializers import (
    ExerciseSetLogSerializer,
    WorkoutSessionCreateSerializer,
    WorkoutSessionDetailSerializer,
    WorkoutSessionListSerializer,
    WorkoutSessionUpdateSerializer,
)


class WorkoutSessionViewSet(viewsets.ModelViewSet):
    """
    /api/sessions/         — list/create
    /api/sessions/{id}/    — retrieve/update/partial_update/destroy
    """

    permission_classes = (permissions.IsAuthenticated, IsSessionOwner)

    def get_queryset(self):
        return (
            WorkoutSession.objects
            .select_related("workout", "student")
            .prefetch_related("set_logs", "set_logs__exercise")
            .filter(student=self.request.user)
        )

    def get_serializer_class(self):
        if self.action == "list":
            return WorkoutSessionListSerializer
        if self.action == "create":
            return WorkoutSessionCreateSerializer
        if self.action in ("update", "partial_update"):
            return WorkoutSessionUpdateSerializer
        return WorkoutSessionDetailSerializer

    def get_serializer_context(self) -> dict:
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


class ExerciseSetLogViewSet(viewsets.ModelViewSet):
    """
    /api/set-logs/{id}/    — retrieve/update/partial_update

    O aluno usa principalmente o PATCH (carga real, marcar concluída).
    Não permite criação/deleção avulsa: as séries são criadas em lote ao
    iniciar a sessão e deletadas quando a sessão é deletada.
    """

    serializer_class = ExerciseSetLogSerializer
    permission_classes = (permissions.IsAuthenticated, IsSessionOwner)
    http_method_names = ("get", "patch", "head", "options")  # bloqueia POST/PUT/DELETE

    def get_queryset(self):
        return (
            ExerciseSetLog.objects
            .select_related("session", "exercise")
            .filter(session__student=self.request.user)
        )
