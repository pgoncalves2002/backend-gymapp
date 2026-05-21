from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_is_billing_exempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="active_until",
            field=models.DateField(
                blank=True,
                help_text=(
                    "Data até quando o aluno tem acesso ao app. Em branco = sem "
                    "limite. Pra alunos com pagamento interno, é atualizado "
                    "automaticamente pelo webhook do Asaas."
                ),
                null=True,
                verbose_name="Acesso até",
            ),
        ),
    ]
