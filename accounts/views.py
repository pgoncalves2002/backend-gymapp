"""
Views do app accounts.

Os endpoints `login/` e `refresh/` reaproveitam as views do simplejwt
(em urls.py); aqui só implementamos `me/` e `register/`.
"""

from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer


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
