"""Serializers do app payments (split)."""

from rest_framework import serializers

from .models import ConnectedAccount, StudentBilling


class ConnectedAccountStatusSerializer(serializers.ModelSerializer):
    """GET /api/payments/connect/status/ — onde o front consulta o onboarding."""

    is_ready = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConnectedAccount
        fields = (
            "asaas_account_id",
            "wallet_id",
            "onboarding_completed",
            "can_receive",
            "is_ready",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OnboardSerializer(serializers.Serializer):
    """POST /api/payments/connect/onboard/ — dados pra criar a subconta."""

    cpf_cnpj = serializers.CharField(max_length=20)
    birth_date = serializers.DateField(required=False)
    company_type = serializers.CharField(required=False, allow_blank=True)
    # Renda anual (CPF) ou faturamento anual (CNPJ), em reais. Asaas exige.
    income_value = serializers.FloatField(min_value=0)
    # Endereço — mínimo pedido pela API do Asaas (postalCode + address).
    postal_code = serializers.CharField(max_length=10)
    address = serializers.CharField(max_length=255)
    address_number = serializers.CharField(max_length=20)
    complement = serializers.CharField(max_length=80, required=False, allow_blank=True)
    province = serializers.CharField(max_length=80, required=False, allow_blank=True)
    # Pix do recebedor — opcional, ajuda em saques diretos pra ele.
    pix_key = serializers.CharField(max_length=80, required=False, allow_blank=True)


class StudentBillingSerializer(serializers.ModelSerializer):
    """Leitura/estado da cobrança do aluno (pro SPA do personal e pro aluno)."""

    is_active_like = serializers.BooleanField(read_only=True)

    class Meta:
        model = StudentBilling
        fields = (
            "id",
            "student",
            "trainer",
            "mode",
            "price_cents",
            "status",
            "is_active_like",
            "current_period_end",
            "last_invoice_url",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "trainer",
            "status",
            "is_active_like",
            "current_period_end",
            "last_invoice_url",
            "created_at",
            "updated_at",
        )


class CreateStudentBillingSerializer(serializers.Serializer):
    """
    POST /api/payments/students/{id}/billing/

    O personal define o valor (em centavos) e o modo. O CPF/CNPJ do aluno é
    necessário pro Asaas; deixamos opcional aqui pra permitir captura via
    formulário do aluno depois.
    """

    price_cents = serializers.IntegerField(min_value=100)  # mínimo R$1,00
    mode = serializers.ChoiceField(
        choices=StudentBilling.Mode.choices,
        default=StudentBilling.Mode.RECURRING,
    )
    cpf_cnpj = serializers.CharField(max_length=20, required=False, allow_blank=True)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)


class RefundSerializer(serializers.Serializer):
    """POST /api/payments/students/{id}/billing/refund/."""

    value_cents = serializers.IntegerField(min_value=0, required=False)
