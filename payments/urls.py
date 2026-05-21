"""URLs do app payments — montadas em /api/payments/."""

from django.urls import path

from .views import (
    ConnectStatusView,
    ConnectWebhookView,
    FinanceBalanceView,
    FinanceTransactionsView,
    FinanceTransferListView,
    FinanceTransferView,
    MyBillingCancelView,
    MyBillingView,
    MyTransactionsView,
    OnboardConnectView,
    RefundBillingView,
    RefundChargeView,
    StudentBillingView,
    StudentChargesView,
    SyncStudentBillingView,
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
        "students/<int:student_id>/billing/sync/",
        SyncStudentBillingView.as_view(),
        name="student-billing-sync",
    ),
    path(
        "students/<int:student_id>/billing/refund/",
        RefundBillingView.as_view(),
        name="student-billing-refund",
    ),
    # Cobranças avulsas extras (coexistem com a mensalidade recorrente)
    path(
        "students/<int:student_id>/charges/",
        StudentChargesView.as_view(),
        name="student-charges",
    ),
    path(
        "students/<int:student_id>/charges/<str:charge_id>/refund/",
        RefundChargeView.as_view(),
        name="student-charge-refund",
    ),
    # Painel financeiro do personal
    path("me/finance/balance/", FinanceBalanceView.as_view(), name="finance-balance"),
    path(
        "me/finance/transactions/",
        FinanceTransactionsView.as_view(),
        name="finance-transactions",
    ),
    path("me/finance/transfer/", FinanceTransferView.as_view(), name="finance-transfer"),
    path(
        "me/finance/transfers/",
        FinanceTransferListView.as_view(),
        name="finance-transfers",
    ),
    # Lado do aluno
    path("me/billing/", MyBillingView.as_view(), name="my-billing"),
    path("me/billing/cancel/", MyBillingCancelView.as_view(), name="my-billing-cancel"),
    path("me/transactions/", MyTransactionsView.as_view(), name="my-transactions"),
    # Webhook
    path("webhook/connect/", ConnectWebhookView.as_view(), name="webhook-connect"),
]
