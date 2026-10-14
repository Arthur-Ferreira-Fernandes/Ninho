from django.db import migrations

from financas.categorias import NOMES_PADRAO, categoria_pelas_regras


def classificar(apps, schema_editor):
    """Cria as categorias padrão de cada família e classifica o que já existe pelas palavras-chave."""
    Familia = apps.get_model("financas", "Familia")
    Categoria = apps.get_model("financas", "Categoria")
    modelos = [
        (apps.get_model("financas", "GastoDia"), "conta__familia", "descricao", {}),
        (apps.get_model("financas", "Lancamento"), "conta__familia", "descricao", {"tipo": "saida"}),
        (apps.get_model("financas", "CompraCartao"), "cartao__conta__familia", "descricao", {}),
        (apps.get_model("financas", "AssinaturaCartao"), "cartao__conta__familia", "descricao", {}),
        (apps.get_model("financas", "ContaFixa"), "conta__familia", "nome", {"tipo": "despesa", "salario": False}),
    ]
    for familia in Familia.objects.all():
        if not familia.categorias_iniciadas:
            for i, nome in enumerate(NOMES_PADRAO):
                Categoria.objects.get_or_create(familia=familia, nome=nome, defaults={"ordem": i})
            familia.categorias_iniciadas = True
            familia.save(update_fields=["categorias_iniciadas"])
        por_nome = {c.nome: c for c in Categoria.objects.filter(familia=familia)}
        for Model, caminho, campo, filtro in modelos:
            for item in Model.objects.filter(**{caminho: familia, "categoria__isnull": True}, **filtro):
                categoria = por_nome.get(categoria_pelas_regras(getattr(item, campo)))
                if categoria:
                    item.categoria = categoria
                    item.save(update_fields=["categoria"])


class Migration(migrations.Migration):
    dependencies = [("financas", "0018_categorias_objetivos")]
    operations = [migrations.RunPython(classificar, migrations.RunPython.noop)]
