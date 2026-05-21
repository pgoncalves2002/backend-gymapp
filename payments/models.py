"""
Split de pagamento — o personal cobra os alunos dele dentro do app e a
FichaGym fica com `PLATFORM_FEE_PERCENT` por transação.

Modelos:
  - `ConnectedAccount`: a subconta Asaas que recebe o repasse (1-1 com o
    personal). Criada via `POST /v3/accounts`. O `wallet_id` é o que entra no
    array `split` das cobranças.
  - `StudentBilling`: a mensalidade que o aluno paga pro personal (1-1 com o
    aluno). Pode ser recorrente (Subscription do Asaas) ou avulsa (Payment).

Coexiste com `billing.Subscription` (a FichaGym cobrando o personal): são
duas receitas independentes — o mesmo personal pode ter ambas.

Gating pra cobrar um aluno:
    trainer.uses_internal_payment
    && trainer.connected_account.is_ready
    && aluno.uses_internal_payment
"""

from django.conf import settings
from django.db import models


class ConnectedAccount(models.Model):
    """
    Subconta Asaas do personal — onde cai o repasse depois do split.

    No fluxo padrão (subconta normal) o personal pode acessar o painel Asaas
    direto. Se o user decidir migrar pra White Label, a `api_key` é usada pra
    fazer chamadas em nome dele.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connected_account",
        verbose_name="Personal",
    )
    asaas_account_id = models.CharField(
        "Asaas Account ID", max_length=64, db_index=True
    )
    wallet_id = models.CharField(
        "Wallet ID (split)",
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Valor passado no array split[].walletId das cobranças.",
    )
    # Sensível: a Asaas devolve a apiKey UMA VEZ na criação. Pra MVP não
    # precisamos usar (modo "subconta normal"); guardamos pro futuro White
    # Label. Em prod considerar criptografar (django-cryptography).
    api_key_encrypted = models.CharField(
        "API key da subconta (criptografada)",
        max_length=255,
        blank=True,
        help_text=(
            "API key da subconta — só usada se o personal operar em White Label. "
            "TODO: criptografar antes de produção (django-cryptography)."
        ),
    )
    onboarding_completed = models.BooleanField(
        "Onboarding concluído", default=False
    )
    can_receive = models.BooleanField(
        "Habilitada a receber",
        default=False,
        help_text="True quando o Asaas reporta a subconta apta a receber pagamentos.",
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizada em", auto_now=True)

    class Meta:
        verbose_name = "Subconta Asaas"
        verbose_name_plural = "Subcontas Asaas"

    def __str__(self) -> str:
        return f"{self.user} — Asaas account {self.asaas_account_id}"

    @property
    def is_ready(self) -> bool:
        """True quando a subconta pode receber cobranças com split."""
        return self.onboarding_completed and self.can_receive and bool(self.wallet_id)


class StudentBilling(models.Model):
    """
    Cobrança do aluno feita pelo personal (mensalidade ou avulsa).

    1-1 com o aluno pra manter a UX simples — se mudar de personal, atualiza
    `trainer` e cria nova subscription/payment no Asaas. O histórico de
    cobranças concretas vive no Asaas (não duplica aqui).
    """

    class Mode(models.TextChoices):
        RECURRING = "recurring", "Recorrente (mensalidade)"
        ONE_OFF = "one_off", "Avulsa"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente (aguardando 1º pagamento)"
        ACTIVE = "active", "Ativa"
        PAST_DUE = "past_due", "Pagamento atrasado"
        CANCELED = "canceled", "Cancelada"
        REFUNDED = "refunded", "Estornada"

    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_as_student",
        verbose_name="Aluno",
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_billings",
        verbose_name="Personal",
    )
    mode = models.CharField(
        "Modo", max_length=12, choices=Mode.choices, default=Mode.RECURRING
    )
    price_cents = models.PositiveIntegerField("Valor (centavos)")
    asaas_customer_id = models.CharField(
        "Asaas Customer ID", max_length=64, blank=True, db_index=True
    )
    asaas_subscription_id = models.CharField(
        "Asaas Subscription ID", max_length=64, blank=True, db_index=True
    )
    asaas_payment_id = models.CharField(
        "Asaas Payment ID (último)", max_length=64, blank=True, db_index=True,
        help_text="Última cobrança gerada (pra estorno avulso).",
    )
    last_invoice_url = models.URLField(
        "Última invoiceUrl",
        max_length=500,
        blank=True,
        help_text="Página de pagamento hospedada — passar pro aluno.",
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    current_period_end = models.DateTimeField(
        "Fim do período atual", null=True, blank=True
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizada em", auto_now=True)

    class Meta:
        verbose_name = "Cobrança de aluno"
        verbose_name_plural = "Cobranças de alunos"

    def __str__(self) -> str:
        return f"{self.student} ← {self.trainer} ({self.get_status_display()})"

    @property
    def is_active_like(self) -> bool:
        return self.status == self.Status.ACTIVE
