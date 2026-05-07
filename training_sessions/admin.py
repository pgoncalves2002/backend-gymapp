from django.contrib import admin
from django.utils.html import format_html

from .models import ExerciseSetLog, WorkoutSession


# ---------------------------------------------------------------------------
# Inline: séries executadas dentro de uma sessão
# ---------------------------------------------------------------------------
class ExerciseSetLogInline(admin.TabularInline):
    model = ExerciseSetLog
    extra = 0
    can_delete = False
    show_change_link = False

    fields = (
        "workout_exercise",
        "set_number",
        "load_kg",
        "completion_display",
    )
    readonly_fields = fields  # tudo readonly — histórico não se edita

    @admin.display(description="Status / Concluída em")
    def completion_display(self, obj: ExerciseSetLog):
        if obj.is_completed and obj.completed_at:
            return format_html(
                '<span style="color:#3a8a3a;">✓ {}</span>',
                obj.completed_at.strftime("%d/%m/%Y %H:%M"),
            )
        return format_html('<span style="color:#888;">— não concluída</span>')

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Sessão de treino
# ---------------------------------------------------------------------------
@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ("workout", "student", "started_at", "finished_at", "status", "elapsed_seconds")
    list_filter = ("status",)
    search_fields = ("workout__name", "student__username")
    autocomplete_fields = ("workout", "student")
    readonly_fields = ("started_at",)
    inlines = [ExerciseSetLogInline]


@admin.register(ExerciseSetLog)
class ExerciseSetLogAdmin(admin.ModelAdmin):
    list_display = ("session", "workout_exercise", "set_number", "load_kg", "is_completed", "completed_at")
    list_filter = ("is_completed",)
    autocomplete_fields = ("session", "workout_exercise")
