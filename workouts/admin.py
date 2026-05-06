from django.contrib import admin

from .models import Exercise, Workout


class ExerciseInline(admin.TabularInline):
    model = Exercise
    extra = 1
    fields = (
        "order", "name", "muscle_group", "sets", "reps",
        "load_kg", "rest_seconds", "demo_gif",
    )
    ordering = ("order",)


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ("name", "focus", "day_label", "student", "trainer", "updated_at")
    list_filter = ("day_label",)
    search_fields = ("name", "focus", "student__username", "trainer__username")
    autocomplete_fields = ("student", "trainer")
    inlines = [ExerciseInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = (
        "name", "workout", "order", "muscle_group",
        "sets", "reps", "load_kg", "has_demo_gif",
    )
    list_filter = ("muscle_group",)
    search_fields = ("name", "muscle_group", "workout__name")
    autocomplete_fields = ("workout",)

    @admin.display(boolean=True, description="GIF?")
    def has_demo_gif(self, obj: Exercise) -> bool:
        return bool(obj.demo_gif)
