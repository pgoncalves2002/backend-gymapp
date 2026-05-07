"""
Migration que (1) altera os choices do User.role pra incluir ADMIN
e (2) adiciona a FK self-referencing User.created_by.

Gerada manualmente porque o desenvolvimento foi feito num ambiente sem o
Django instalado, então o `makemigrations` não foi rodado localmente.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("student", "Aluno"),
                    ("trainer", "Personal Trainer"),
                    ("admin", "Administrador"),
                ],
                db_index=True,
                default="student",
                max_length=10,
                verbose_name="Função",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="users_created",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Cadastrado por",
            ),
        ),
    ]
