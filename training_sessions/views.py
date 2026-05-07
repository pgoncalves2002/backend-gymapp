"""
ViewSets do app training_sessions.

Regras de visibilidade:
    - Aluno acessa apenas as próprias sessões.
    - Trainer não acessa sessões de execução (são privadas do aluno).
    - Aluno só pode atualizar séries (`load_kg`, `is_completed`) das próprias sessões.
"""

from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

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
            .prefetch_related("set_logs", "set_logs__workout_exercise")
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

    def create(self, request, *args, **kwargs):
        """
        POST /api/sessions/ — cria a session + auto-cria os set_logs.

        Response: usa o DetailSerializer (não o CreateSerializer) pra que o
        cliente receba a session COMPLETA com `set_logs` aninhados — assim
        ele consegue mapear os IDs sem precisar de um GET extra.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        detail = WorkoutSessionDetailSerializer(
            instance, context=self.get_serializer_context()
        )
        headers = self.get_success_headers(serializer.data)
        return Response(detail.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        """
        PUT/PATCH /api/sessions/{id}/

        Mesma estratégia do `create`: usa o UpdateSerializer pra validar
        (campos limitados), mas retorna o DetailSerializer pra que o cliente
        receba a session completa — evita decoders quebrados em apps que
        esperam o payload full.
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        # Recarrega pra pegar campos auto-setados (ex.: finished_at em status terminal).
        instance.refresh_from_db()
        detail = WorkoutSessionDetailSerializer(
            instance, context=self.get_serializer_context()
        )
        return Response(detail.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


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
            .select_related("session", "workout_exercise")
            .filter(session__student=self.request.user)
        )
