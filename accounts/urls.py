"""
URLs do app accounts — montadas em /api/auth/ pelo gym_api/urls.py.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .sync_views import SyncView
from .views import (
    ActiveUserTokenRefreshView,
    LoginView,
    MeView,
    RegisterView,
    StudentsViewSet,
)

app_name = "accounts"

router = DefaultRouter()
router.register(r"students", StudentsViewSet, basename="student")

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    # Custom refresh: rejeita user com is_active=False (impede aluno
    # bloqueado de renovar access token).
    path("refresh/", ActiveUserTokenRefreshView.as_view(), name="refresh"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
