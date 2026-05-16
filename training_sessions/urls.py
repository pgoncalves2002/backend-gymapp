"""
URLs do app training_sessions — montadas em /api/ pelo gym_api/urls.py.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .dashboard_views import (
    MyMetricsView,
    MySessionDetailView,
    MySessionsView,
    TrainerDashboardView,
    TrainerSessionDetailView,
    TrainerStudentMetricsView,
    TrainerStudentSessionsView,
)
from .views import ExerciseSetLogViewSet, WorkoutSessionViewSet

router = DefaultRouter()
router.register(r"sessions", WorkoutSessionViewSet, basename="session")
router.register(r"set-logs", ExerciseSetLogViewSet, basename="set-log")

app_name = "training_sessions"

urlpatterns = [
    path("", include(router.urls)),

    # Dashboard do trainer — agregações de desempenho.
    # Endpoints separados do CRUD de sessions porque permission é diferente
    # (IsTrainer aqui vs IsSessionOwner no CRUD) e a lógica é toda agregação.
    path(
        "trainers/me/dashboard/",
        TrainerDashboardView.as_view(),
        name="trainer-dashboard",
    ),
    path(
        "trainers/me/students/<int:student_id>/metrics/",
        TrainerStudentMetricsView.as_view(),
        name="trainer-student-metrics",
    ),
    # Histórico bruto: lista paginada de sessions + detail de 1 sessão com
    # set_logs por exercício. Aba "Histórico" da StudentPage no SPA.
    path(
        "trainers/me/students/<int:student_id>/sessions/",
        TrainerStudentSessionsView.as_view(),
        name="trainer-student-sessions",
    ),
    path(
        "trainers/me/sessions/<uuid:session_id>/detail/",
        TrainerSessionDetailView.as_view(),
        name="trainer-session-detail",
    ),

    # "Self" endpoints — aluno consultando os PRÓPRIOS dados (sem precisar
    # passar student_id no path). Mesmo payload das views do trainer; só
    # muda a permission (IsStudent) e o scope (request.user).
    path(
        "students/me/metrics/",
        MyMetricsView.as_view(),
        name="my-metrics",
    ),
    path(
        "students/me/sessions/",
        MySessionsView.as_view(),
        name="my-sessions",
    ),
    path(
        "students/me/sessions/<uuid:session_id>/detail/",
        MySessionDetailView.as_view(),
        name="my-session-detail",
    ),
]
