from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "stripe_customer_id",
                    models.CharField(
                        db_index=True, max_length=64, verbose_name="Stripe Customer ID"
                    ),
                ),
                (
                    "stripe_subscription_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=64,
                        verbose_name="Stripe Subscription ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("incomplete", "Incompleta (aguardando 1º pagamento)"),
                            ("incomplete_expired", "Expirada sem pagar"),
                            ("trialing", "Em teste"),
                            ("active", "Ativa"),
                            ("past_due", "Pagamento atrasado"),
                            ("canceled", "Cancelada"),
                            ("unpaid", "Não paga"),
                        ],
                        db_index=True,
                        default="incomplete",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "plan",
                    models.CharField(
                        choices=[("monthly", "Mensal"), ("annual", "Anual")],
                        max_length=10,
                        verbose_name="Plano",
                    ),
                ),
                (
                    "price_id",
                    models.CharField(
                        blank=True, max_length=64, verbose_name="Stripe Price ID"
                    ),
                ),
                (
                    "current_period_end",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Fim do período atual"
                    ),
                ),
                (
                    "cancel_at_period_end",
                    models.BooleanField(
                        default=False, verbose_name="Cancela no fim do período"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Criada em"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Atualizada em"),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Personal",
                    ),
                ),
            ],
            options={
                "verbose_name": "Assinatura",
                "verbose_name_plural": "Assinaturas",
            },
        ),
    ]
