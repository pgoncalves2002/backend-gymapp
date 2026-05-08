"""
Views do app accounts.

Os endpoints `login/` e `refresh/` reaproveitam as views do simplejwt
(em urls.py); aqui implementamos `me/`, `register/` e o `StudentsViewSet`
usado pelo SPA do personal.
"""

from rest_framework import filters, generics, permissions, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .permissions import IsTrainer
from .serializers import (
    LoginSerializer,
    RegisterSerializer,
    StudentSerializer,
    UserSerializer,
)


class LoginView(TokenObtainPairView):
    """POST /api/auth/login/ — devolve access/refresh + dados do user."""

    serializer_class = LoginSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — cria um aluno (sempre role=student)."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = (permissions.AllowAny,)


class MeView(generics.RetrieveUpdateAPIView):
    """
    GET    /api/auth/me/ — dados do user logado.
    PATCH  /api/auth/me/ — atualizar display_name, email, birth_date.
    Não permite mudar role nem username pelo próprio endpoint.
    """

    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self) -> User:
        return self.request.user


# ---------------------------------------------------------------------------
# Alunos do trainer — usado pelo SPA do personal
# ---------------------------------------------------------------------------
class StudentsViewSet(viewsets.ModelViewSet):
    """
    /api/auth/students/        — list/create os alunos do trainer logado
    /api/auth/students/{id}/   — retrieve/update/partial_update/destroy

    Visibilidade:
        - Trainer: vê apenas alunos onde `created_by == self`.
        - Admin/staff: vê todos os alunos.
        - Student: 403 (não é caso de uso desse endpoint).

    No POST, `created_by` é setado automaticamente como o trainer logado
    e `role` é forçado a STUDENT (via serializer).
    """

    serializer_class = StudentSerializer
    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    # DRF OrderingFilter expõe ?ordering=<campo>. Os campos abaixo são
    # whitelist — qualquer outro retorna 400. Default mantém alfabético.
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ("display_name", "username", "date_joined", "updated_at")
    ordering = ("display_name", "username")

    def get_queryset(self):
        user = self.request.user
        qs = User.objects.filter(role=User.Role.STUDENT)
        if user.is_staff or user.is_superuser or user.has_full_access:
            return qs
        return qs.filter(created_by=user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        user = self.request.user
        if not (user.has_full_access or instance.created_by_id == user.id):
            raise PermissionDenied("Só o trainer que cadastrou pode editar este aluno.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.has_full_access or instance.created_by_id == user.id):
            raise PermissionDenied("Só o trainer que cadastrou pode remover este aluno.")
        instance.delete()
