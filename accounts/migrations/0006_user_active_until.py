from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_phone_user_uses_internal_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="active_until",
            field=models.DateField(
                blank=True,
                help_text=(
                    "Data até quando o aluno tem acesso ao app. Em branco = sem "
                    "limite. Trainer define manualmente."
                ),
                null=True,
                verbose_name="Acesso até",
            ),
        ),
    ]
