"""
Signals do app accounts.

Sincroniza Groups e flags do User automaticamente baseado no `role`:
    - role=trainer  → entra no Group 'Trainers' + is_staff=True
    - role=admin    → entra no Group 'Admins'   + is_staff=True
    - role=student  → sem Group + is_staff=False
    - is_superuser  → bypassa tudo (Django dá todas perms via flag)
"""

from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


GROUP_BY_ROLE = {
    User.Role.TRAINER: "Trainers",
    User.Role.ADMIN: "Admins",
}


@receiver(post_save, sender=User)
def sync_user_groups_and_staff(sender, instance: User, created: bool, **kwargs):
    """Mantém groups + is_staff alinhados com o role atual."""
    if instance.is_superuser:
        return  # superuser não precisa de groups

    desired_group_name = GROUP_BY_ROLE.get(instance.role)
    desired_is_staff = desired_group_name is not None  # trainer/admin → staff

    # Atualiza is_staff sem disparar o signal de novo (update em vez de save)
    if instance.is_staff != desired_is_staff:
        User.objects.filter(pk=instance.pk).update(is_staff=desired_is_staff)
        instance.is_staff = desired_is_staff  # reflete em memória

    # Sincroniza groups: remove os "nossos" e adiciona o correto
    managed_names = list(GROUP_BY_ROLE.values())
    instance.groups.remove(*Group.objects.filter(name__in=managed_names))
    if desired_group_name:
        group, _ = Group.objects.get_or_create(name=desired_group_name)
        instance.groups.add(group)
