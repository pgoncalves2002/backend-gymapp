"""
URLs do app workouts — montadas em /api/ pelo gym_api/urls.py.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ExerciseViewSet,
    SetPresetViewSet,
    WorkoutExerciseViewSet,
    WorkoutViewSet,
)

router = DefaultRouter()
router.register(r"workouts", WorkoutViewSet, basename="workout")
router.register(r"exercises", ExerciseViewSet, basename="exercise")
router.register(r"workout-exercises", WorkoutExerciseViewSet, basename="workout-exercise")
router.register(r"set-presets", SetPresetViewSet, basename="set-preset")

app_name = "workouts"

urlpatterns = [
    path("", include(router.urls)),
]
