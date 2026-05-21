"""
Serializers do app accounts.
"""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Representação pública do usuário (sem senha).

    Usado em GET /api/auth/me/ e dentro do login response. `uses_internal_payment`
    é read-only aqui — pra alterar, usa-se endpoint do admin (trainer) ou do
    trainer (aluno), nunca o próprio usuário se editar a flag.
    """

    is_trainer = serializers.BooleanField(read_only=True)
    is_student = serializers.BooleanField(read_only=True)
    # Cobrança do app (assinatura do personal) — read-only, pro SPA montar
    # o paywall/upgrade sem chamada extra.
    has_active_subscription = serializers.BooleanField(read_only=True)
    student_count = serializers.IntegerField(read_only=True)
    # Validade do acesso do aluno — pro app mostrar banner/bloqueio.
    is_within_validity = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "phone",
            "display_name",
            "role",
            "birth_date",
            "uses_internal_payment",
            "is_billing_exempt",
            "is_trainer",
            "is_student",
            "has_active_subscription",
            "student_count",
            "active_until",
            "is_within_validity",
        )
        read_only_fields = (
            "id", "role", "uses_internal_payment", "is_billing_exempt",
            "is_trainer", "is_student", "has_active_subscription",
            "student_count", "active_until", "is_within_validity",
        )


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
    is_within_validity = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "phone",
            "password",
            "display_name",
            "birth_date",
            "role",
            "is_active",
            "uses_internal_payment",
            "active_until",
            "is_within_validity",
            "is_trainer",
            "is_student",
            "date_joined",
            "updated_at",
        )
        read_only_fields = (
            "id", "role", "is_trainer", "is_student",
            "is_within_validity",
            "date_joined", "updated_at",
        )
        # `email` e `phone` ficam REQUIRED apenas no create. No update
        # (PATCH), são opcionais — não quebra trainers editando alunos antigos
        # sem mexer nesses campos. Lógica de obrigatoriedade no create
        # acontece em `validate()` abaixo, porque DRF não tem "required apenas
        # em create".
        extra_kwargs = {
            "email": {"required": False, "allow_blank": True},
            "phone": {"required": False, "allow_blank": True},
            "display_name": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs: dict) -> dict:
        """
        Obriga email + phone em NOVOS cadastros. Usuários já existentes podem
        ser editados sem precisar preencher (deixamos os antigos sem quebrar).
        """
        if self.instance is None:  # create
            if not attrs.get("email"):
                raise serializers.ValidationError(
                    {"email": "E-mail é obrigatório no cadastro."}
                )
            if not attrs.get("phone"):
                raise serializers.ValidationError(
                    {"phone": "Telefone é obrigatório no cadastro (usado pro WhatsApp)."}
                )
        return attrs

    def validate_active_until(self, value):
        """
        Trainer só pode editar `active_until` MANUAL quando o aluno NÃO usa
        pagamento interno. Pra alunos com pagamento interno, esse campo é
        atualizado automaticamente pelo webhook do Asaas (estende o acesso
        até o `current_period_end` da subscription) — qualquer edição manual
        seria sobrescrita logo no próximo pagamento, confundindo o trainer.
        """
        # No create, o aluno ainda não existe — value passa.
        if self.instance is None:
            return value
        if not self.instance.uses_internal_payment:
            return value
        # Pagamento interno ligado: só admin/superuser sobrescreve.
        request = self.context.get("request")
        trainer = getattr(request, "user", None) if request else None
        if trainer and trainer.has_full_access:
            return value
        # Se o valor não está mudando, deixa passar (no caso de PATCH parcial
        # mandar o valor atual).
        if value == self.instance.active_until:
            return value
        raise serializers.ValidationError(
            "Alunos com pagamento interno têm a validade atualizada "
            "automaticamente pelo webhook do Asaas. Não dá pra editar à mão."
        )

    def validate_uses_internal_payment(self, value: bool) -> bool:
        """
        Aluno só pode ter pagamento ATIVO se o trainer dele também tem.
        Trainer libera por aluno; admin libera por trainer.
        """
        if not value:
            return False  # desativar sempre permitido
        request = self.context.get("request")
        trainer = getattr(request, "user", None) if request else None
        # `request.user` é o trainer logado nesse contexto (StudentsViewSet).
        # Pra trainers com has_full_access (admin), pula a gating.
        if trainer is None:
            return value
        if trainer.has_full_access:
            return value
        if not trainer.uses_internal_payment:
            raise serializers.ValidationError(
                "Você ainda não está habilitado a usar o pagamento interno. "
                "Fale com o admin pra liberar antes de ativar pros alunos."
            )
        return value

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

    Aceita EMAIL ou USERNAME no campo `username` da request. A heurística:
    se o input contém `@`, tenta resolver pra um username via lookup por
    email; só então delega pra autenticação padrão do SimpleJWT (que opera
    no USERNAME_FIELD do User, = `username` por default).

    Fallback explícito: se o lookup por email não encontrar nada, o input
    é passado adiante como username puro — assim usuários ANTIGOS sem email
    cadastrado seguem podendo logar com username original.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Claims customizados — disponíveis via decode no app
        token["role"] = user.role
        token["display_name"] = user.display_name or user.username
        return token

    def validate(self, attrs: dict) -> dict:
        username_field = self.username_field  # = "username"
        raw = attrs.get(username_field, "") or ""

        # Heurística simples: input com "@" é tratado como email.
        # Edge case: usuário literalmente cadastrado com username contendo
        # "@" (raro, mas válido no Django). Nesse caso o lookup por email
        # falha, o fallback usa o input como username — não quebra.
        if "@" in raw:
            try:
                user = User.objects.get(email__iexact=raw)
                attrs[username_field] = user.username
            except User.DoesNotExist:
                # Mantém o input como username. SimpleJWT vai retornar 401
                # se também não existir como username (comportamento normal).
                pass
            except User.MultipleObjectsReturned:
                # Mesmo email em mais de uma conta — não dá pra desambiguar.
                # Recusa pra evitar fazer login na conta errada.
                raise serializers.ValidationError(
                    "Mais de uma conta usa esse e-mail. Entre com o nome de usuário."
                )

        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data
