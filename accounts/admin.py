from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin de usuários com escopo por papel.

    Visibilidade:
        - Superuser ou role=Admin: vê TODOS os usuários.
        - Trainer: vê ele mesmo + alunos que cadastrou.
        - Outros: nada.

    Comportamento:
        - Trainer ao criar usuário: created_by = ele; força aluno; sem is_staff.
        - Trainer não pode promover usuário a staff/superuser/admin.
    """

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Perfil",
            {
                "fields": (
                    "role",
                    "display_name",
                    "phone",
                    "birth_date",
                    "created_by",
                ),
            },
        ),
        (
            "Cobrança",
            {
                "fields": ("is_billing_exempt", "uses_internal_payment"),
                "description": (
                    "<b>is_billing_exempt</b>: personal isento da assinatura "
                    "do app (cortesia/grandfather).<br>"
                    "<b>uses_internal_payment</b>: habilita o personal a "
                    "cobrar os alunos pelo app (split via Asaas)."
                ),
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Perfil", {"fields": ("role", "display_name", "phone")}),
    )
    list_display = (
        "username",
        "display_name",
        "email",
        "role",
        "uses_internal_payment",
        "is_billing_exempt",
        "created_by",
        "is_active",
    )
    list_filter = BaseUserAdmin.list_filter + (
        "role",
        "uses_internal_payment",
        "is_billing_exempt",
    )
    search_fields = ("username", "display_name", "email")

    # MARK: - Visibilidade
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.has_full_access:
            return qs
        # Trainer (e qualquer outro staff sem full access) vê: ele mesmo + alunos que criou
        return qs.filter(Q(pk=request.user.pk) | Q(created_by=request.user))

    # MARK: - Permissões granulares
    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.has_full_access:
            return True
        # Trainer só pode editar alunos que cadastrou.
        # NÃO pode editar o próprio cadastro (regra de negócio).
        # Pra trocar senha continua usando /admin/password_change/ direto.
        return obj.created_by_id == request.user.id and obj.pk != request.user.pk

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.has_full_access:
            return True
        # Trainer não deleta a si mesmo, só os alunos que criou.
        return obj.pk != request.user.pk and obj.created_by_id == request.user.id

    # MARK: - Auto-set + travas
    def save_model(self, request, obj, form, change):
        # Trainer só cadastra alunos próprios
        if not change and not obj.created_by_id and not request.user.has_full_access:
            obj.created_by = request.user
        # Trainer (sem full access) NUNCA consegue criar staff/superuser/admin/trainer.
        if not request.user.has_full_access:
            obj.is_superuser = False
            obj.role = User.Role.STUDENT
            # is_staff é gerenciado pelo signal post_save baseado no role.
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not request.user.has_full_access:
            # Trainer não decide papel/staff/superuser/created_by/groups nem
            # mexe nas flags de cobrança (admin libera).
            ro += [
                "is_staff", "is_superuser", "role", "created_by",
                "user_permissions", "groups",
                "is_billing_exempt", "uses_internal_payment",
            ]
        return ro
