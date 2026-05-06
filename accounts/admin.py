from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin customizado: adiciona role/display_name/birth_date no formulário."""

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Perfil", {"fields": ("role", "display_name", "birth_date")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Perfil", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "display_name", "email", "role", "is_active")
    list_filter = BaseUserAdmin.list_filter + ("role",)
    search_fields = ("username", "display_name", "email")
