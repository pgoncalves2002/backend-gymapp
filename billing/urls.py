"""URLs do app billing — montadas em /api/billing/ pelo gym_api/urls.py."""

from django.urls import path

from .views import (
    AsaasWebhookView,
    CancelSubscriptionView,
    SubscribeView,
    SubscriptionDetailView,
    SyncSubscriptionView,
    TrainerSignupView,
)

app_name = "billing"

urlpatterns = [
    path("signup/", TrainerSignupView.as_view(), name="signup"),
    path("subscribe/", SubscribeView.as_view(), name="subscribe"),
    path("subscription/", SubscriptionDetailView.as_view(), name="subscription"),
    path("sync/", SyncSubscriptionView.as_view(), name="sync"),
    path("cancel/", CancelSubscriptionView.as_view(), name="cancel"),
    path("webhook/", AsaasWebhookView.as_view(), name="webhook"),
]
