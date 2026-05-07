"""
Management command pra criar/atualizar os Groups 'Trainers' e 'Admins'
com as Permissions corretas.

Idempotente — pode ser rodado quantas vezes quiser. Útil:
    - Após primeiro deploy (caso a data migration tenha rodado antes
      das Permissions serem geradas pelos post_migrate de outros apps).
    - Após adicionar models novos no projeto.

Uso:
    docker compose exec web python manage.py setup_groups
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand


# (app_label, model_name, [actions])
PERMISSION_SPECS = [
    ("accounts",          "user",           ["view", "add", "change", "delete"]),
    ("workouts",          "exercise",       ["view", "add", "change", "delete"]),
    ("workouts",          "workout",        ["view", "add", "change", "delete"]),
    ("workouts",          "workoutexercise",["view", "add", "change", "delete"]),
    ("training_sessions", "workoutsession", ["view"]),
    ("training_sessions", "exercisesetlog", ["view"]),
]


class Command(BaseCommand):
    help = "Cria/atualiza os Groups 'Trainers' e 'Admins' com as Permissions corretas."

    def handle(self, *args, **options):
        perms = self._collect_permissions()

        for group_name in ("Trainers", "Admins"):
            group, created = Group.objects.get_or_create(name=group_name)
            group.permissions.set(perms)
            verb = "Criado" if created else "Atualizado"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} grupo '{group_name}' com {group.permissions.count()} permissões"
            ))

        self.stdout.write(self.style.SUCCESS("✓ Setup concluído"))

    def _collect_permissions(self) -> list[Permission]:
        perms: list[Permission] = []
        missing: list[str] = []

        for app_label, model_name, actions in PERMISSION_SPECS:
            try:
                ct = ContentType.objects.get(app_label=app_label, model=model_name)
            except ContentType.DoesNotExist:
                missing.append(f"{app_label}.{model_name} (ContentType ausente)")
                continue

            for action in actions:
                codename = f"{action}_{model_name}"
                try:
                    p = Permission.objects.get(content_type=ct, codename=codename)
                    perms.append(p)
                except Permission.DoesNotExist:
                    missing.append(f"{app_label}.{codename}")

        if missing:
            self.stdout.write(self.style.WARNING(
                "Permissões faltando (rode `migrate` e tente de novo):"
            ))
            for m in missing:
                self.stdout.write(f"  - {m}")

        return perms
