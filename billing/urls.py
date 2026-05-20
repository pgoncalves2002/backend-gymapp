"""URLs do app billing — montadas em /api/billing/ pelo gym_api/urls.py."""

from django.urls import path

from .views import (
    BillingPortalView,
    StripeWebhookView,
    SubscribeView,
    SubscriptionDetailView,
    TrainerSignupView,
)

app_name = "billing"

urlpatterns = [
    path("signup/", TrainerSignupView.as_view(), name="signup"),
    path("subscribe/", SubscribeView.as_view(), name="subscribe"),
    path("subscription/", SubscriptionDetailView.as_view(), name="subscription"),
    path("portal/", BillingPortalView.as_view(), name="portal"),
    path("webhook/", StripeWebhookView.as_view(), name="webhook"),
]
