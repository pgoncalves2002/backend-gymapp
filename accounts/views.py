"""
Views do app accounts.

Os endpoints `login/` e `refresh/` reaproveitam as views do simplejwt
(em urls.py); aqui implementamos `me/`, `register/` e o `StudentsViewSet`
usado pelo SPA do personal.
"""

import secrets

from rest_framework import filters, generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import User
from .permissions import IsTrainer
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    StudentSerializer,
    UserSerializer,
)


class PaymentRequired(APIException):
    """402 — limite do plano grátis atingido; precisa assinar pra continuar."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = (
        "Você atingiu o limite de alunos do plano grátis. "
        "Assine um plano pra cadastrar mais alunos."
    )
    default_code = "payment_required"


class LoginView(TokenObtainPairView):
    """POST /api/auth/login/ — devolve access/refresh + dados do user.

    Django ModelBackend já bloqueia login se `user.is_active=False` (mesma
    flag usada pelo admin pelo checkbox "Ativo"). Resposta nesse caso é 401.
    """

    serializer_class = LoginSerializer


class ActiveUserTokenRefreshView(TokenRefreshView):
    """POST /api/auth/refresh/ — troca refresh por novo access.

    Override do TokenRefreshView padrão pra rejeitar usuários inativos.
    Sem isso, um aluno bloqueado (`is_active=False`) podia continuar usando
    seu refresh token até a expiração (14 dias) — `simplejwt` valida só
    a assinatura do JWT, não consulta o user no DB.

    Comportamento:
        - Refresh válido + user ativo → 200 com novo access (e refresh
          rotacionado se ROTATE_REFRESH_TOKENS=True).
        - Refresh válido + user inativo (bloqueado) → 401 com mensagem
          clara, e tenta blacklistar o refresh pra cortar imediato.
        - Refresh inválido/expirado → 401 (comportamento padrão).
    """

    def post(self, request, *args, **kwargs):
        refresh_str = request.data.get("refresh")
        if refresh_str:
            try:
                token = RefreshToken(refresh_str)
                user_id = token.payload.get("user_id")
                if user_id:
                    user = User.objects.filter(pk=user_id).only("id", "is_active").first()
                    if user is not None and not user.is_active:
                        # Tenta blacklistar pra invalidar imediato — só roda
                        # se o app de blacklist estiver instalado; caso contrário,
                        # rejeita do mesmo jeito mas o token ainda fica "vivo"
                        # até expirar pelo iat/exp.
                        try:
                            token.blacklist()
                        except (AttributeError, Exception):
                            pass
                        raise InvalidToken("Conta bloqueada. Fale com seu personal trainer.")
            except InvalidToken:
                raise
            except Exception:
                # Token malformado/expirado: deixa o super lidar (vai 401)
                pass

        return super().post(request, *args, **kwargs)


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — cria um aluno (sempre role=student)."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = (permissions.AllowAny,)


class MeView(generics.RetrieveUpdateAPIView):
    """
    GET    /api/auth/me/ — dados do user logado.
    PATCH  /api/auth/me/ — atualizar display_name, email, phone, birth_date.
    Não permite mudar role nem username pelo próprio endpoint.
    """

    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self) -> User:
        return self.request.user


class MyTrainerView(generics.RetrieveAPIView):
    """
    GET /api/auth/me/trainer/

    Retorna os dados do trainer do aluno logado (lookup via `created_by`).
    Endpoint usado pelo app iOS na aba "Personal" pra mostrar nome, foto
    (futuro), email, telefone e gerar link do WhatsApp.

    Campos expostos: os mesmos do UserSerializer — incluindo email e
    phone (necessário pro link wa.me). Sem dados sensíveis (senha,
    is_superuser, etc).

    Erros:
      - 404 se o aluno foi cadastrado SEM created_by (usuários antigos
        antes do schema multi-tenant, ou raros admin-criados).
    """

    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self) -> User:
        user = self.request.user
        # Só faz sentido pra aluno. Se um trainer tentar bater nesse endpoint,
        # 404 — é mais limpo que 403 (não é violação, só não tem o quê retornar).
        if user.role != User.Role.STUDENT:
            from rest_framework.exceptions import NotFound
            raise NotFound("Apenas alunos têm trainer associado.")
        trainer = user.created_by
        if trainer is None or trainer.role != User.Role.TRAINER:
            from rest_framework.exceptions import NotFound
            raise NotFound(
                "Este aluno não tem um trainer associado. "
                "Peça pro admin vincular um trainer."
            )
        return trainer


class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/ — troca a senha do próprio usuário.

    Body: { "current_password": "...", "new_password": "..." }

    Self-service tanto pro trainer (SPA) quanto pro aluno (mobile). Não
    invalidamos os JWTs existentes — a senha não está embutida no token,
    então o access/refresh seguem válidos. UX: o usuário continua logado.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Senha alterada com sucesso."},
            status=status.HTTP_200_OK,
        )


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
        # SEGURANÇA (multi-tenant): trainer com `is_staff=True` (pra acessar
        # /admin/ do Django) NÃO pode ver alunos de outros trainers. Só
        # `has_full_access` (= role=admin OU is_superuser) tem visão global.
        # is_staff sozinho é só permissão de UI do admin, não autorização de
        # bypass do scoping por trainer.
        if user.has_full_access:
            return qs
        return qs.filter(created_by=user)

    def create(self, request, *args, **kwargs):
        """
        POST /api/auth/students/

        Cria o aluno e gera uma SENHA TEMPORÁRIA aleatória — mesma política do
        reset-password. O trainer NÃO escolhe a senha; ela aparece UMA ÚNICA
        VEZ no response pro trainer copiar e passar pro aluno.

        Por que aleatório (e não o trainer escolher)?
            - Trainer não tem ciência da senha pessoal do aluno depois
              (privacidade básica + reduce blast radius).
            - O aluno entra com a temp e troca pela própria via "Alterar senha".

        Por que sobrescrever `create()` (em vez de `perform_create`)?
            - Precisamos misturar a senha gerada (apenas-uma-vez) no response,
              o que o fluxo default do DRF não permite. `perform_create` só
              recebe o instance — não controla o response.
        """
        # Gate freemium: no plano grátis o personal só pode ter até
        # settings.FREE_STUDENT_LIMIT alunos. Pra mais, precisa assinar.
        # (admin/superuser e isentos passam direto via has_active_subscription.)
        if not request.user.can_add_student():
            raise PaymentRequired()

        # Valida tudo MENOS a senha — a senha vem do backend.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        temp_password = self._generate_temp_password()
        student = serializer.save(
            created_by=request.user,
            password=temp_password,  # entra no validated_data via save(**kwargs)
        )

        # Re-serializa pra pegar todos os campos read-only (id, date_joined…)
        # e injetar a temp_password ao lado.
        output = self.get_serializer(student).data
        output["temp_password"] = temp_password
        output["detail"] = (
            "Aluno cadastrado. Esta senha será mostrada apenas agora — "
            "passe pro aluno e oriente a troca no primeiro login."
        )
        return Response(output, status=status.HTTP_201_CREATED)

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

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """
        POST /api/auth/students/{id}/reset-password/

        Reseta a senha do aluno pra um valor ALEATÓRIO gerado pelo backend.
        Retorna a senha em texto plano UMA ÚNICA VEZ — o trainer deve copiar
        e passar pro aluno, que vai usar pra fazer login e depois trocar pelo
        fluxo normal de change-password.

        Por que aleatório (em vez do trainer escolher)?
            - Trainer NÃO escolhe pra não ter ciência da senha pessoal do aluno
              depois (privacidade básica + reduce blast radius se conta do
              trainer for comprometida).
            - O aluno é OBRIGADO a trocar logo no primeiro login (UX = entrar
              com a temp, ir em "Alterar senha", trocar).

        Permissões:
            - Trainer que CADASTROU o aluno (created_by == request.user)
            - OU admin/superuser

        Não invalida o JWT do aluno (caso ele esteja logado em algum dispositivo).
        Mas, na prática, se o motivo do reset é "aluno esqueceu a senha",
        ele já não consegue logar pra renovar token — quando expirar a sessão
        atual ele cai e precisa logar com a temp.
        """
        student = self.get_object()  # já aplica permission + scoping
        user = request.user
        if not (user.has_full_access or student.created_by_id == user.id):
            raise PermissionDenied(
                "Só o trainer que cadastrou pode resetar a senha deste aluno."
            )

        temp_password = self._generate_temp_password()
        student.set_password(temp_password)
        student.save(update_fields=["password"])

        return Response(
            {
                "temp_password": temp_password,
                "detail": (
                    "Senha resetada. Passe esta senha pro aluno — ela só será "
                    "mostrada agora. Recomende que ele troque assim que logar."
                ),
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _generate_temp_password(length: int = 12) -> str:
        """
        Gera senha aleatória com alfabeto sem caracteres ambíguos
        (sem I, l, 1, O, 0) — facilita comunicação verbal entre trainer
        e aluno. 12 chars no alfabeto abaixo dá ~70 bits de entropia.

        Usa `secrets` (não `random`) — gerador criptograficamente seguro.
        """
        alphabet = (
            "ABCDEFGHJKLMNPQRSTUVWXYZ"   # sem I, O
            "abcdefghjkmnpqrstuvwxyz"     # sem i, l, o
            "23456789"                    # sem 0, 1
        )
        return "".join(secrets.choice(alphabet) for _ in range(length))
