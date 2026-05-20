"""
Assinatura do PERSONAL (cobrança do FichaGym pelo uso do app).

ATENÇÃO — não confundir com `User.uses_internal_payment`:
  - `Subscription` (este modelo): a FichaGym cobra o personal pra usar o app.
  - `uses_internal_payment` (em accounts.User): o personal cobra os ALUNOS
    dele por dentro do app (outra feature, do backlog).

Modelo freemium:
  - Personal sem `Subscription` ativa = plano GRÁTIS (limite de alunos em
    settings.FREE_STUDENT_LIMIT, default 1). Não exige cartão.
  - Pra ter mais alunos, o personal assina (mensal/anual) via Stripe. Aí
    ganha uma `Subscription` cujo `status` espelha o status da Stripe.
"""

from django.conf import settings
from django.db import models


class Subscription(models.Model):
    """1-pra-1 com o User (personal). Espelha o objeto Subscription da Stripe."""

    class Status(models.TextChoices):
        # Espelha os status da Stripe de propósito — o webhook só copia.
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
    stripe_customer_id = models.CharField(
        "Stripe Customer ID", max_length=64, db_index=True
    )
    stripe_subscription_id = models.CharField(
        "Stripe Subscription ID", max_length=64, blank=True, db_index=True
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.INCOMPLETE,
        db_index=True,
    )
    plan = models.CharField("Plano", max_length=10, choices=Plan.choices)
    price_id = models.CharField("Stripe Price ID", max_length=64, blank=True)
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
