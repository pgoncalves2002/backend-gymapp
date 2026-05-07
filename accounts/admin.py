from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin de usuários com escopo por trainer.

    Visibilidade:
        - Superuser: vê todos os usuários
        - Trainer (is_staff=True, role=trainer): vê ele mesmo + alunos que cadastrou
        - Outros: nada (não acessam o admin)

    Comportamento:
        - Trainer ao criar usuário: created_by é setado automaticamente como ele.
        - Trainer não pode editar/deletar usuários que não criou.
    """

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Perfil", {"fields": ("role", "display_name", "birth_date", "created_by")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Perfil", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "display_name", "email", "role", "created_by", "is_active")
    list_filter = BaseUserAdmin.list_filter + ("role",)
    search_fields = ("username", "display_name", "email")

    # MARK: - Visibilidade
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Trainer vê: ele mesmo + alunos que criou
        return qs.filter(Q(pk=request.user.pk) | Q(created_by=request.user))

    # MARK: - Permissões granulares
    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        # Trainer só edita ele mesmo ou quem criou
        return obj.pk == request.user.pk or obj.created_by_id == request.user.id

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        # Trainer não deleta ele mesmo, só os alunos que criou
        return obj.pk != request.user.pk and obj.created_by_id == request.user.id

    # MARK: - Auto-set created_by + travas de campos
    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id and not request.user.is_superuser:
            obj.created_by = request.user
        # Trainer não pode criar superuser nem outro trainer.
        if not request.user.is_superuser:
            obj.is_superuser = False
            obj.is_staff = False
            obj.role = User.Role.STUDENT  # força aluno
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Trainer não decide papel/staff/superuser/created_by
            ro += ["is_staff", "is_superuser", "role", "created_by", "user_permissions", "groups"]
        return ro
