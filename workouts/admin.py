from django.contrib import admin

from .models import Exercise, Workout, WorkoutExercise


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
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
            "description": "Marque <b>Público</b> pra que todos os trainers possam usar.",
        }),
        ("Auditoria", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

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


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ("name", "focus", "day_label", "student", "trainer", "updated_at")
    list_filter = ("day_label",)
    search_fields = ("name", "focus", "student__username", "trainer__username")
    autocomplete_fields = ("student", "trainer")
    inlines = [WorkoutExerciseInline]
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# Item da ficha (visualização separada — útil pra debug)
# ---------------------------------------------------------------------------
@admin.register(WorkoutExercise)
class WorkoutExerciseAdmin(admin.ModelAdmin):
    list_display = ("workout", "order", "exercise", "sets", "reps", "load_kg", "rest_seconds")
    list_filter = ("workout__day_label",)
    search_fields = ("workout__name", "exercise__name")
    autocomplete_fields = ("workout", "exercise")
