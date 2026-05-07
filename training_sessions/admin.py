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
        "reps_done",
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

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.has_full_access:
            return qs
        # Trainer vê as sessões dos alunos que ele cadastrou.
        return qs.filter(student__created_by=request.user)


# Nota: ExerciseSetLog NÃO é registrado como admin próprio.
# Séries executadas são SEMPRE acessadas via inline dentro de WorkoutSession
# (com renderização readonly + indicador "não concluída"). Manter um admin
# separado polui o menu sem ganho — e exigia registrar WorkoutExercise como
# admin só pra `autocomplete_fields`, o que duplicava entrada no menu.
