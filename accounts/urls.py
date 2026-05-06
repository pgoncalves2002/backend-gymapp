"""
URLs do app accounts — montadas em /api/auth/ pelo gym_api/urls.py.
"""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .sync_views import SyncView
from .views import LoginView, MeView, RegisterView

app_name = "accounts"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
]
