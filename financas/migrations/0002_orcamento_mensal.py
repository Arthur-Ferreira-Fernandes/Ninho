from decimal import Decimal

from django.db import migrations, models


def diario_para_mensal(apps, schema_editor):
    Conta = apps.get_model("financas", "Conta")
    for conta in Conta.objects.all():
        conta.modo_gasto = "mensal"
        conta.gasto_mensal = conta.gasto_diario * 30
        conta.save(update_fields=["modo_gasto", "gasto_mensal"])


def mensal_para_diario(apps, schema_editor):
    Conta = apps.get_model("financas", "Conta")
    for conta in Conta.objects.all():
        if conta.modo_gasto == "percentual":
            mensal = (conta.salario or 0) * (conta.percentual_gasto or 0) / 100
        else:
            mensal = conta.gasto_mensal
        conta.gasto_diario = (Decimal(mensal) / 30).quantize(Decimal("0.01"))
        conta.save(update_fields=["gasto_diario"])


class Migration(migrations.Migration):

    dependencies = [
        ("financas", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conta",
            name="modo_gasto",
            field=models.CharField(choices=[("percentual", "% do salário"), ("mensal", "Valor fixo por mês")],
                                   default="mensal", max_length=10),
        ),
        migrations.AddField(
            model_name="conta",
            name="salario",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="conta",
            name="percentual_gasto",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True,
                                      help_text="Porcentagem do salário reservada para o dia a dia."),
        ),
        migrations.AddField(
            model_name="conta",
            name="gasto_mensal",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12,
                                      help_text="Quanto pode ser gasto no dia a dia durante o mês."),
        ),
        migrations.RunPython(diario_para_mensal, mensal_para_diario),
        migrations.RemoveField(model_name="conta", name="gasto_diario"),
    ]
