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
            name="ConnectedAccount",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "asaas_account_id",
                    models.CharField(db_index=True, max_length=64, verbose_name="Asaas Account ID"),
                ),
                (
                    "wallet_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Valor passado no array split[].walletId das cobranças.",
                        max_length=64,
                        verbose_name="Wallet ID (split)",
                    ),
                ),
                (
                    "api_key_encrypted",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "API key da subconta — só usada se o personal operar em White Label. "
                            "TODO: criptografar antes de produção (django-cryptography)."
                        ),
                        max_length=255,
                        verbose_name="API key da subconta (criptografada)",
                    ),
                ),
                (
                    "onboarding_completed",
                    models.BooleanField(default=False, verbose_name="Onboarding concluído"),
                ),
                (
                    "can_receive",
                    models.BooleanField(
                        default=False,
                        help_text="True quando o Asaas reporta a subconta apta a receber pagamentos.",
                        verbose_name="Habilitada a receber",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Criada em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Atualizada em")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connected_account",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Personal",
                    ),
                ),
            ],
            options={
                "verbose_name": "Subconta Asaas",
                "verbose_name_plural": "Subcontas Asaas",
            },
        ),
        migrations.CreateModel(
            name="StudentBilling",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[("recurring", "Recorrente (mensalidade)"), ("one_off", "Avulsa")],
                        default="recurring",
                        max_length=12,
                        verbose_name="Modo",
                    ),
                ),
                ("price_cents", models.PositiveIntegerField(verbose_name="Valor (centavos)")),
                (
                    "asaas_customer_id",
                    models.CharField(
                        blank=True, db_index=True, max_length=64, verbose_name="Asaas Customer ID"
                    ),
                ),
                (
                    "asaas_subscription_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=64,
                        verbose_name="Asaas Subscription ID",
                    ),
                ),
                (
                    "asaas_payment_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Última cobrança gerada (pra estorno avulso).",
                        max_length=64,
                        verbose_name="Asaas Payment ID (último)",
                    ),
                ),
                (
                    "last_invoice_url",
                    models.URLField(
                        blank=True,
                        help_text="Página de pagamento hospedada — passar pro aluno.",
                        max_length=500,
                        verbose_name="Última invoiceUrl",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendente (aguardando 1º pagamento)"),
                            ("active", "Ativa"),
                            ("past_due", "Pagamento atrasado"),
                            ("canceled", "Cancelada"),
                            ("refunded", "Estornada"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "current_period_end",
                    models.DateTimeField(blank=True, null=True, verbose_name="Fim do período atual"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Criada em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Atualizada em")),
                (
                    "student",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="billing_as_student",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Aluno",
                    ),
                ),
                (
                    "trainer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_billings",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Personal",
                    ),
                ),
            ],
            options={
                "verbose_name": "Cobrança de aluno",
                "verbose_name_plural": "Cobranças de alunos",
            },
        ),
    ]
