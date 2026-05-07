from django.contrib import admin
from django.db.models import Q

from accounts.admin import StaffAccessibleAdminMixin
from accounts.models import User

from .models import Exercise, Workout, WorkoutExercise


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
@admin.register(Exercise)
class ExerciseAdmin(StaffAccessibleAdminMixin, admin.ModelAdmin):
    """
    Catálogo de exercícios.

    Visibilidade:
        - Superuser: vê tudo, pode marcar `is_public`.
        - Trainer: vê os públicos + os privados que ele criou.
                   NÃO pode marcar `is_public` (campo readonly pra ele).
    """

    list_display = (
        "name", "muscle_group", "is_public",
        "created_by", "has_demo_gif", "updated_at",
    )
    list_filter = ("muscle_group", "is_public")
    search_fields = ("name", "muscle_group", "created_by__username")
    autocomplete_fields = ("created_by",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("name", "muscle_group", "default_technique_note", "demo_gif"),
        }),
        ("Visibilidade", {
            "fields": ("is_public", "created_by"),
            "description": "<b>Apenas o superuser</b> pode marcar como público.",
        }),
        ("Auditoria", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(Q(is_public=True) | Q(created_by=request.user))

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Trainer não pode tornar exercício público nem mexer no created_by.
            ro += ["is_public", "created_by"]
        return ro

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        # Trava: não-superuser nunca consegue salvar is_public=True.
        if not request.user.is_superuser:
            obj.is_public = False
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        # Trainer só edita exercícios que criou (públicos não, mesmo se ele criou
        # — se virou público, pertence ao "catálogo geral" curado pelo admin).
        return obj.created_by_id == request.user.id and not obj.is_public

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return obj.created_by_id == request.user.id and not obj.is_public

    @admin.display(boolean=True, description="GIF?")
    def has_demo_gif(self, obj: Exercise) -> bool:
        return bool(obj.demo_gif)


# ---------------------------------------------------------------------------
# Ficha + inline de itens
# ---------------------------------------------------------------------------
class WorkoutExerciseInline(admin.TabularInline):
    model = WorkoutExercise
    extra = 1
    fields = ("order", "exercise", "sets", "reps", "load_kg", "rest_seconds", "technique_note")
    autocomplete_fields = ("exercise",)
    ordering = ("order",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # No autocomplete de exercise, mostra só o que o trainer pode usar.
        if db_field.name == "exercise" and not request.user.is_superuser:
            kwargs["queryset"] = Exercise.objects.filter(
                Q(is_public=True) | Q(created_by=request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Workout)
class WorkoutAdmin(StaffAccessibleAdminMixin, admin.ModelAdmin):
    """
    Fichas de treino.

    Visibilidade:
        - Superuser: vê todas.
        - Trainer: vê só as que criou (`trainer=self`).
    """

    list_display = ("name", "focus", "day_label", "student", "trainer", "updated_at")
    list_filter = ("day_label",)
    search_fields = ("name", "focus", "student__username", "trainer__username")
    autocomplete_fields = ("student", "trainer")
    inlines = [WorkoutExerciseInline]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(trainer=request.user)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "student":
                # Trainer escolhe APENAS entre os alunos que ele cadastrou.
                kwargs["queryset"] = User.objects.filter(
                    role=User.Role.STUDENT,
                    created_by=request.user,
                )
            elif db_field.name == "trainer":
                # Trainer só pode ser ele mesmo.
                kwargs["queryset"] = User.objects.filter(pk=request.user.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Não-superuser sempre é o trainer da própria ficha.
        if not request.user.is_superuser:
            obj.trainer = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return obj.trainer_id == request.user.id

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return obj.trainer_id == request.user.id


# Nota: WorkoutExercise NÃO é registrado como admin próprio. Ele aparece
# apenas como inline dentro de WorkoutAdmin.
