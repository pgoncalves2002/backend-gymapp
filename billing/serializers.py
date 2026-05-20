"""Serializers do app billing."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Subscription

User = get_user_model()


class TrainerSignupSerializer(serializers.ModelSerializer):
    """
    Auto-cadastro PÚBLICO de personal trainer (plano grátis, sem cartão).

    Diferente do RegisterSerializer de aluno (em accounts), aqui o role é
    forçado a TRAINER. Não cria nada na Stripe — o personal entra no plano
    grátis e só assina depois, quando quiser passar do limite de alunos.
    """

    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = ("username", "email", "password", "display_name", "phone")
        extra_kwargs = {
            "email": {"required": True},
            "display_name": {"required": True},
        }

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Este nome de usuário já está em uso.")
        return value

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Já existe uma conta com este e-mail.")
        return value

    def create(self, validated_data: dict) -> User:
        validated_data["role"] = User.Role.TRAINER  # garante via backend
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class SubscribeSerializer(serializers.Serializer):
    """Body de POST /api/billing/subscribe/."""

    plan = serializers.ChoiceField(choices=Subscription.Plan.choices)


class SubscriptionSerializer(serializers.ModelSerializer):
    """Leitura do estado da assinatura (pro paywall e tela "Minha assinatura")."""

    is_active_like = serializers.BooleanField(read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "status",
            "plan",
            "current_period_end",
            "cancel_at_period_end",
            "is_active_like",
        )
        read_only_fields = fields
