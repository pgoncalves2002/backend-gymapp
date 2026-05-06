from django.contrib import admin

from .models import ExerciseSetLog, WorkoutSession


class ExerciseSetLogInline(admin.TabularInline):
    model = ExerciseSetLog
    extra = 0
    fields = ("exercise", "set_number", "load_kg", "is_completed", "completed_at")
    autocomplete_fields = ("exercise",)


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
    list_display = ("session", "exercise", "set_number", "load_kg", "is_completed", "completed_at")
    list_filter = ("is_completed",)
    autocomplete_fields = ("session", "exercise")
