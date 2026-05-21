"""URLs do app payments — montadas em /api/payments/."""

from django.urls import path

from .views import (
    ConnectStatusView,
    ConnectWebhookView,
    MyBillingView,
    OnboardConnectView,
    RefundBillingView,
    StudentBillingView,
)

app_name = "payments"

urlpatterns = [
    # Subconta do personal (split)
    path("connect/onboard/", OnboardConnectView.as_view(), name="connect-onboard"),
    path("connect/status/", ConnectStatusView.as_view(), name="connect-status"),
    # Cobrança do aluno (com split)
    path(
        "students/<int:student_id>/billing/",
        StudentBillingView.as_view(),
        name="student-billing",
    ),
    path(
        "students/<int:student_id>/billing/refund/",
        RefundBillingView.as_view(),
        name="student-billing-refund",
    ),
    # Lado do aluno
    path("me/billing/", MyBillingView.as_view(), name="my-billing"),
    # Webhook
    path("webhook/connect/", ConnectWebhookView.as_view(), name="webhook-connect"),
]
