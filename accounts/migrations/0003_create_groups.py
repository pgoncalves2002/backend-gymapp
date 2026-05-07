"""
Data migration: cria os Groups 'Trainers' e 'Admins' com permissions
apropriadas. Idempotente — pode rodar várias vezes sem duplicar.

A 0002 (que será gerada por makemigrations pra adicionar created_by + ADMIN
no role) precisa rodar antes desta. Por isso o `dependencies` inclui ela.

Observação: usamos `apps.get_model` em vez de imports diretos pra que a
migration funcione mesmo se os models forem alterados no futuro.
"""

from django.db import migrations


# Permissions concedidas. Modelo: "{app_label}.{action}_{model}".
# Tanto Trainer quanto Admin recebem o conjunto inteiro — o ESCOPO de
# "quem vê o quê" continua sendo controlado pelos ModelAdmins via
# get_queryset/has_change_permission.
COMMON_CODENAMES = [
    # Contas
    "view_user", "add_user", "change_user", "delete_user",
    # Catálogo de exercícios
    "view_exercise", "add_exercise", "change_exercise", "delete_exercise",
    # Fichas de treino
    "view_workout", "add_workout", "change_workout", "delete_workout",
    # Itens da ficha (acessados via inline mas precisa da perm)
    "view_workoutexercise", "add_workoutexercise",
    "change_workoutexercise", "delete_workoutexercise",
    # Sessões executadas (apenas leitura via admin — escrita é pelo app)
    "view_workoutsession",
    "view_exercisesetlog",
]


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    # Pega os Permission objects pelos codenames esperados.
    perms = list(Permission.objects.filter(codename__in=COMMON_CODENAMES))

    for group_name in ("Trainers", "Admins"):
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.set(perms)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["Trainers", "Admins"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        # Garante que a 0002 (alter role choices + add created_by) rodou.
        ("accounts", "0002_user_created_by_alter_user_role"),
        # Garante que content_types e permissions já existem pros models nossos.
        ("workouts", "0001_initial"),
        ("training_sessions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_groups, reverse_code=remove_groups),
    ]
