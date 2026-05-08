"""
Modelo de usuário customizado.

Por que estender AbstractUser?
- Padrão recomendado pela documentação oficial do Django para projetos novos.
- Permite adicionar campos (`role`, `display_name`, `birth_date`) sem precisar
  de uma tabela `Profile` separada (menos joins, menos código).
- Migrar `User` depois é doloroso — começar com custom user evita esse débito.
"""

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
