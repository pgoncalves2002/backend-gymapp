"""
Serializers do app accounts.
"""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Representação pública do usuário (sem senha)."""

    is_trainer = serializers.BooleanField(read_only=True)
    is_student = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "display_name",
            "role",
            "birth_date",
            "is_trainer",
            "is_student",
        )
        read_only_fields = ("id", "role", "is_trainer", "is_student")


class RegisterSerializer(serializers.ModelSerializer):
    """
    Registro de aluno. Trainers são criados via admin.
    Força role=student aqui pra evitar privilege escalation pelo endpoint público.
    """

    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = ("username", "email", "password", "display_name", "birth_date")
        extra_kwargs = {
            "email": {"required": True},
            "display_name": {"required": True},
        }

    def create(self, validated_data: dict) -> User:
        validated_data["role"] = User.Role.STUDENT  # garante via backend
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(TokenObtainPairSerializer):
    """
    Custom JWT login: além do access/refresh, devolve os dados do user.
    Reduz uma chamada extra do app Swift logo após o login.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Claims customizados — disponíveis via decode no app
        token["role"] = user.role
        token["display_name"] = user.display_name or user.username
        return token

    def validate(self, attrs: dict) -> dict:
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data
