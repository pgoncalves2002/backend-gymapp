from django.db import migrations, models


def grandfather_existing_trainers(apps, schema_editor):
    """
    Personais que já existiam antes da cobrança continuam usando de graça.
    Marca todos os trainers atuais como isentos. Novos cadastros (via
    /api/billing/signup/) entram com is_billing_exempt=False (default).
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="trainer").update(is_billing_exempt=True)


def undo_grandfather(apps, schema_editor):
    # Reverter não desmarca ninguém (seria destrutivo e sem ganho). No-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_phone_user_uses_internal_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_billing_exempt",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Se ativo, este personal usa o app sem assinatura paga "
                    "(cortesia ou conta antiga grandfatherizada)."
                ),
                verbose_name="Isento de cobrança do app?",
            ),
        ),
        migrations.RunPython(grandfather_existing_trainers, undo_grandfather),
    ]
