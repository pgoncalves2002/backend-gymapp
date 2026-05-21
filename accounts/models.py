"""
Modelo de usuário customizado.

Por que estender AbstractUser?
- Padrão recomendado pela documentação oficial do Django para projetos novos.
- Permite adicionar campos (`role`, `display_name`, `birth_date`) sem precisar
  de uma tabela `Profile` separada (menos joins, menos código).
- Migrar `User` depois é doloroso — começar com custom user evita esse débito.
"""

from datetime import date

from django.contrib.auth.models import AbstractUser
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
    # Fonte: trainer define manualmente na tela do aluno. No futuro, quando
    # o pagamento interno entrar em produção, alunos com `uses_internal_payment`
    # vão ter esse campo atualizado automaticamente pelo gateway.
    active_until = models.DateField(
        "Acesso até",
        null=True, blank=True,
        help_text=(
            "Data até quando o aluno tem acesso ao app. Em branco = sem "
            "limite. Trainer define manualmente."
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
