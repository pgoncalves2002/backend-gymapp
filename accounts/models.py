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
