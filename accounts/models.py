"""
Modelo de usuário customizado.

Por que estender AbstractUser?
- Padrão recomendado pela documentação oficial do Django para projetos novos.
- Permite adicionar campos (`role`, `display_name`, `birth_date`) sem precisar
  de uma tabela `Profile` separada (menos joins, menos código).
- Migrar `User` depois é doloroso — começar com custom user evita esse débito.
"""

from datetime import date

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "Aluno"
        TRAINER = "trainer", "Personal Trainer"
        ADMIN = "admin", "Administrador"

    role = models.CharField(
        "Função",
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
    )
    display_name = models.CharField("Nome de exibição", max_length=80, blank=True)
    birth_date = models.DateField("Data de nascimento", null=True, blank=True)

    # Telefone — usado pra montar link `wa.me/<numero>` no aluno/personal.
    # `blank=True` pra não quebrar usuários cadastrados antes desta migration;
    # nos NOVOS cadastros o serializer força preencher.
    # Formato livre (string) — validação só remove não-dígitos pra o link.
    phone = models.CharField(
        "Telefone (WhatsApp)",
        max_length=20,
        blank=True,
        help_text="Usado para gerar link do WhatsApp. Ex.: +55 11 91234-5678",
    )

    # Flag de pagamento via app — dupla camada de gating:
    #   - No TRAINER: definido pelo admin. Habilita o trainer a usar o sistema.
    #   - No STUDENT: definido pelo trainer. Habilita ESTE aluno individualmente.
    # Aluno só pode ter True se o trainer dele (created_by) também tem True —
    # validação fica no serializer/view, não no model (regra de negócio que
    # depende do `created_by`, não de constraint local).
    uses_internal_payment = models.BooleanField(
        "Usa pagamento interno do app?",
        default=False,
        db_index=True,
        help_text=(
            "Se ativo, este usuário usa o sistema de pagamento interno. "
            "Pra alunos, só pode ser ativo se o trainer também estiver ativo."
        ),
    )

    # Isenta o personal da cobrança do app (plano cortesia / grandfather dos
    # personais que já existiam antes do go-live da cobrança). Quando True,
    # `has_active_subscription` retorna True sem precisar de Subscription.
    is_billing_exempt = models.BooleanField(
        "Isento de cobrança do app?",
        default=False,
        help_text=(
            "Se ativo, este personal usa o app sem assinatura paga "
            "(cortesia ou conta antiga grandfatherizada)."
        ),
    )

    # Quem cadastrou este usuário (geralmente o trainer dono dos alunos).
    # Usada pra escopar o admin: trainer só vê os alunos que ele criou.
    created_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="users_created",
        verbose_name="Cadastrado por",
    )

    # Quando este registro foi modificado pela última vez. Usado pelo SPA
    # do personal pra ordenar a lista de alunos por "última edição".
    # `null=True` pra não quebrar registros anteriores à migration.
    updated_at = models.DateTimeField(
        "Atualizado em",
        auto_now=True,
        null=True, blank=True,
    )

    # Validade do ACESSO do aluno (não da ficha — ver Workout.valid_until).
    # null = sem limite (acesso liberado pra sempre).
    # >= hoje = ativo.
    # < hoje = bloqueado (aluno consegue logar mas não vê fichas).
    #
    # Fonte:
    #   - Aluno SEM pagamento interno: trainer define manualmente.
    #   - Aluno COM pagamento interno: atualizado automaticamente pelo
    #     webhook do Asaas quando a cobrança é confirmada (estende até
    #     o `current_period_end` da subscription).
    #
    # Cobranças do tipo "renovação com sucesso" expandem `active_until`
    # antes de o anterior vencer → o aluno não perde acesso.
    active_until = models.DateField(
        "Acesso até",
        null=True, blank=True,
        help_text=(
            "Data até quando o aluno tem acesso ao app. Em branco = sem "
            "limite. Pra alunos com pagamento interno, é atualizado "
            "automaticamente pelo webhook do Asaas."
        ),
    )

    class Meta:
        ordering = ["username"]
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"

    def __str__(self) -> str:
        label = self.display_name or self.username
        return f"{label} ({self.get_role_display()})"

    @property
    def is_trainer(self) -> bool:
        return self.role == self.Role.TRAINER

    @property
    def is_student(self) -> bool:
        return self.role == self.Role.STUDENT

    @property
    def is_admin_role(self) -> bool:
        """Role 'Administrador' (não confundir com `is_superuser` do Django)."""
        return self.role == self.Role.ADMIN

    @property
    def has_full_access(self) -> bool:
        """Quem pode tudo: superuser do Django ou role=Administrador."""
        return self.is_superuser or self.is_admin_role

    # -----------------------------------------------------------------------
    # Cobrança do app (assinatura do personal) — ver app `billing`.
    # Não confundir com `uses_internal_payment` (personal cobra os alunos).
    # -----------------------------------------------------------------------
    @property
    def has_active_subscription(self) -> bool:
        """
        True se o personal pode usar o app além do limite grátis.
        Admin/superuser e isentos (grandfather) sempre passam.
        """
        if self.has_full_access or self.is_billing_exempt:
            return True
        try:
            return self.subscription.is_active_like
        except ObjectDoesNotExist:
            return False

    @property
    def student_count(self) -> int:
        """Quantos alunos este personal cadastrou (scope multi-tenant)."""
        return User.objects.filter(
            created_by=self, role=User.Role.STUDENT
        ).count()

    def can_add_student(self) -> bool:
        """
        Regra do freemium: com assinatura ativa (ou isento/admin), sem limite.
        No plano grátis, pode ter até `settings.FREE_STUDENT_LIMIT` alunos.
        """
        if self.has_active_subscription:
            return True
        free_limit = getattr(settings, "FREE_STUDENT_LIMIT", 1)
        return self.student_count < free_limit

    # -----------------------------------------------------------------------
    # Validade do acesso do aluno
    # -----------------------------------------------------------------------
    @property
    def is_within_validity(self) -> bool:
        """
        True se o aluno está dentro da validade do acesso.

        Sem `active_until` setado = sem limite (True).
        Admin/superuser ignoram validade.
        Trainer não tem validade (só aluno usa esse mecanismo).
        """
        if self.has_full_access:
            return True
        if self.role != self.Role.STUDENT:
            return True
        if not self.active_until:
            return True
        return self.active_until >= date.today()
