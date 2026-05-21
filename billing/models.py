"""
Assinatura do PERSONAL (cobrança do FichaGym pelo uso do app).

ATENÇÃO — não confundir com `User.uses_internal_payment`:
  - `Subscription` (este modelo): a FichaGym cobra o personal pra usar o app.
  - `uses_internal_payment` (em accounts.User): o personal cobra os ALUNOS
    dele por dentro do app (feature de split — ver app `payments`).

Modelo freemium:
  - Personal sem `Subscription` ativa = plano GRÁTIS (limite de alunos em
    settings.FREE_STUDENT_LIMIT, default 1). Não exige cartão.
  - Pra ter mais alunos, o personal assina (mensal/anual) via Asaas. Aí
    ganha uma `Subscription` cujo `status` reflete o status do Asaas.
"""

from django.conf import settings
from django.db import models


class Subscription(models.Model):
    """1-pra-1 com o User (personal). Espelha o objeto Subscription do Asaas."""

    class Status(models.TextChoices):
        # Status normalizados — mapeados a partir do Asaas no handler do webhook.
        INCOMPLETE = "incomplete", "Incompleta (aguardando 1º pagamento)"
        INCOMPLETE_EXPIRED = "incomplete_expired", "Expirada sem pagar"
        TRIALING = "trialing", "Em teste"
        ACTIVE = "active", "Ativa"
        PAST_DUE = "past_due", "Pagamento atrasado"
        CANCELED = "canceled", "Cancelada"
        UNPAID = "unpaid", "Não paga"

    class Plan(models.TextChoices):
        MONTHLY = "monthly", "Mensal"
        ANNUAL = "annual", "Anual"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="Personal",
    )
    asaas_customer_id = models.CharField(
        "Asaas Customer ID", max_length=64, db_index=True, blank=True
    )
    asaas_subscription_id = models.CharField(
        "Asaas Subscription ID", max_length=64, blank=True, db_index=True
    )
    # URL da fatura hospedada (`invoiceUrl`) — o front redireciona pra cá pra
    # o personal pagar. Pode ser renovada por novo POST /subscribe/.
    last_invoice_url = models.URLField(
        "Última invoiceUrl", max_length=500, blank=True
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.INCOMPLETE,
        db_index=True,
    )
    plan = models.CharField("Plano", max_length=10, choices=Plan.choices)
    price_cents = models.PositiveIntegerField(
        "Valor (centavos)", default=0,
        help_text="Snapshot do valor cobrado quando a assinatura foi criada.",
    )
    current_period_end = models.DateTimeField(
        "Fim do período atual", null=True, blank=True
    )
    cancel_at_period_end = models.BooleanField(
        "Cancela no fim do período", default=False
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizada em", auto_now=True)

    class Meta:
        verbose_name = "Assinatura"
        verbose_name_plural = "Assinaturas"

    def __str__(self) -> str:
        return f"{self.user} — {self.get_status_display()} ({self.plan})"

    @property
    def is_active_like(self) -> bool:
        """Status em que o personal PODE usar o app além do limite grátis."""
        return self.status in {self.Status.ACTIVE, self.Status.TRIALING}
