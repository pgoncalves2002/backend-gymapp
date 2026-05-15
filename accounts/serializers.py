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


class StudentSerializer(serializers.ModelSerializer):
    """
    Aluno do trainer — usado pelo SPA do personal pra listar/criar/editar
    seus alunos. Diferente do RegisterSerializer (que é endpoint público
    de auto-cadastro), aqui a criação é feita pelo trainer logado e
    `created_by` é setado automaticamente no view.

    Senha:
        - No CREATE, o trainer NÃO escolhe a senha — o view gera uma temporária
          aleatória e a retorna no response (mesma lógica do reset-password).
          Por isso `password` não aparece nos fields como writable.
        - No UPDATE, password é opcional e segue write-only (caso o trainer
          precise forçar uma senha manualmente; raro, mas mantido).

    `is_active`: writable pra permitir bloqueio/desbloqueio. User com
    is_active=False não consegue logar (Django bloqueia auth).

    `date_joined` e `updated_at`: read-only, usados pra ordenação no SPA.
    """

    # Mantém password no input só pra UPDATE (PATCH). No CREATE o view
    # ignora qualquer password que vier e gera uma aleatória.
    password = serializers.CharField(
        write_only=True,
        required=False,
        validators=[validate_password],
        style={"input_type": "password"},
        allow_blank=False,
    )
    is_trainer = serializers.BooleanField(read_only=True)
    is_student = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "password",
            "display_name",
            "birth_date",
            "role",
            "is_active",
            "is_trainer",
            "is_student",
            "date_joined",
            "updated_at",
        )
        read_only_fields = (
            "id", "role", "is_trainer", "is_student",
            "date_joined", "updated_at",
        )
        extra_kwargs = {
            "email": {"required": False, "allow_blank": True},
            "display_name": {"required": False, "allow_blank": True},
        }

    def create(self, validated_data: dict) -> User:
        # No fluxo novo, o view passa `password` (já gerada pelo backend) no
        # validated_data antes de chamar serializer.save(). Se por algum
        # motivo não vier, ainda quebra explícito — é um bug, não cadastro
        # bug-prone.
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError(
                {"password": "Senha temporária não fornecida pelo view."}
            )
        validated_data["role"] = User.Role.STUDENT  # garante via backend
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance: User, validated_data: dict) -> User:
        password = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    """
    Troca de senha do próprio usuário logado.

    Exige a senha atual pra evitar takeover via session hijacking (alguém com
    o access token ainda precisa saber a senha atual).
    """

    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Senha atual incorreta.")
        return value

    def validate_new_password(self, value: str) -> str:
        # Aplica os AUTH_PASSWORD_VALIDATORS do Django (min length, comum etc.).
        # Passa o user pra detectar similaridade com username/email.
        validate_password(value, self.context["request"].user)
        return value

    def validate(self, attrs: dict) -> dict:
        # Bloqueia "trocar" pela mesma senha — pouco útil e confunde o user.
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "A nova senha precisa ser diferente da atual."}
            )
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
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
