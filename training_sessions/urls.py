"""
URLs do app training_sessions — montadas em /api/ pelo gym_api/urls.py.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExerciseSetLogViewSet, WorkoutSessionViewSet

router = DefaultRouter()
router.register(r"sessions", WorkoutSessionViewSet, basename="session")
router.register(r"set-logs", ExerciseSetLogViewSet, basename="set-log")

app_name = "training_sessions"

urlpatterns = [
    path("", include(router.urls)),
]
