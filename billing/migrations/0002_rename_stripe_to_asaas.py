"""
Renomeia campos Stripe → Asaas no Subscription.

Como nenhuma `Subscription` real foi persistida em prod ainda (a feature
ficou nas branches `feat/cobranca-stripe`/`feat/cobranca-checkout` sem push
nem deploy — ver CONTEXT.md seção 15), é seguro **dropar e recriar** os
campos em vez de tentar copiar dados.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(model_name="subscription", name="stripe_customer_id"),
        migrations.RemoveField(model_name="subscription", name="stripe_subscription_id"),
        migrations.RemoveField(model_name="subscription", name="price_id"),
        migrations.AddField(
            model_name="subscription",
            name="asaas_customer_id",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, verbose_name="Asaas Customer ID"
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="asaas_subscription_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=64,
                verbose_name="Asaas Subscription ID",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="last_invoice_url",
            field=models.URLField(blank=True, max_length=500, verbose_name="Última invoiceUrl"),
        ),
        migrations.AddField(
            model_name="subscription",
            name="price_cents",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Snapshot do valor cobrado quando a assinatura foi criada.",
                verbose_name="Valor (centavos)",
            ),
        ),
    ]
