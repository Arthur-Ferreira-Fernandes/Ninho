from django.db import migrations, models


def salario_para_pagamentos(apps, schema_editor):
    """O salário era um valor único na conta; vira pagamento(s) marcados como salário."""
    Conta = apps.get_model("financas", "Conta")
    ContaFixa = apps.get_model("financas", "ContaFixa")
    for conta in Conta.objects.exclude(salario=None).exclude(salario=0):
        existente = ContaFixa.objects.filter(conta=conta, tipo="receita", nome="Salário").first()
        if existente:
            existente.salario = True
            existente.save(update_fields=["salario"])
        else:
            # o dia não era conhecido; dia 1 até a pessoa ajustar
            ContaFixa.objects.create(conta=conta, nome="Salário", valor=conta.salario, dia=1,
                                     tipo="receita", salario=True)


def pagamentos_para_salario(apps, schema_editor):
    Conta = apps.get_model("financas", "Conta")
    for conta in Conta.objects.all():
        pagamentos = conta.fixas.filter(salario=True, ativa=True)
        conta.salario = sum((p.valor for p in pagamentos), 0) or None
        conta.save(update_fields=["salario"])


class Migration(migrations.Migration):

    dependencies = [
        ("financas", "0002_orcamento_mensal"),
    ]

    operations = [
        migrations.AddField(
            model_name="contafixa",
            name="salario",
            field=models.BooleanField(default=False,
                                      help_text="Pagamento do salário; a soma deles é a base do % de gasto."),
        ),
        migrations.RunPython(salario_para_pagamentos, pagamentos_para_salario),
        migrations.RemoveField(model_name="conta", name="salario"),
    ]
