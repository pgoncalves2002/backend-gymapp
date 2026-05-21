from django.contrib import admin

from .models import ConnectedAccount, StudentBilling


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "asaas_account_id",
        "wallet_id",
        "onboarding_completed",
        "can_receive",
        "updated_at",
    )
    list_filter = ("onboarding_completed", "can_receive")
    search_fields = ("user__username", "user__email", "asaas_account_id", "wallet_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StudentBilling)
class StudentBillingAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "trainer",
        "mode",
        "price_cents",
        "status",
        "current_period_end",
        "updated_at",
    )
    list_filter = ("status", "mode")
    search_fields = (
        "student__username",
        "trainer__username",
        "asaas_customer_id",
        "asaas_subscription_id",
        "asaas_payment_id",
    )
    readonly_fields = ("created_at", "updated_at")
