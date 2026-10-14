"""Categorias, análise (insights), objetivos e importação de extrato."""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import importacao, insights
from .forms import CategoriaForm, ObjetivoForm
from .models import AssinaturaCartao, Aporte, Categoria, CompraCartao, Conta, ContaFixa, GastoDia, Lancamento, Objetivo
from .templatetags.dinheiro import brl
from .views import _aba, com_familia

# tudo o que pode ter categoria: (Model, caminho até a família)
COM_CATEGORIA = [
    (GastoDia, "conta__familia"),
    (Lancamento, "conta__familia"),
    (CompraCartao, "cartao__conta__familia"),
    (AssinaturaCartao, "cartao__conta__familia"),
    (ContaFixa, "conta__familia"),
]


def _usos_por_categoria(familia):
    usos = {}
    for Model, caminho in COM_CATEGORIA:
        for linha in (Model.objects.filter(**{caminho: familia}, categoria__isnull=False)
                      .values("categoria").annotate(n=Count("id"))):
            usos[linha["categoria"]] = usos.get(linha["categoria"], 0) + linha["n"]
    return usos


# ---------- Categorias ----------

@com_familia
def categorias(request):
    familia = request.familia
    lista = list(familia.lista_categorias())
    form = CategoriaForm(request.POST or None, familia=familia, prefix="nova")
    if form.is_valid():
        categoria = form.save(commit=False)
        categoria.familia = familia
        categoria.ordem = max((c.ordem for c in lista), default=0) + 1
        categoria.save()
        messages.success(request, f"Categoria “{categoria.nome}” criada.")
        return redirect("financas:categorias")
    usos = _usos_por_categoria(familia)
    for c in lista:
        c.usos = usos.get(c.pk, 0)
        c.form = CategoriaForm(instance=c, familia=familia, prefix=f"c{c.pk}")
    return render(request, "financas/categorias.html", {
        "categorias": lista, "form": form, "voltar_conta": familia.contas.first(),
    })


@require_POST
@com_familia
def categoria_editar(request, categoria_pk):
    categoria = get_object_or_404(Categoria, pk=categoria_pk, familia=request.familia)
    form = CategoriaForm(request.POST, instance=categoria, familia=request.familia, prefix=f"c{categoria.pk}")
    if form.is_valid():
        form.save()
        messages.success(request, f"Categoria “{categoria.nome}” salva.")
    else:
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
    return redirect("financas:categorias")


@require_POST
@com_familia
def categoria_excluir(request, categoria_pk):
    categoria = get_object_or_404(Categoria, pk=categoria_pk, familia=request.familia)
    messages.success(request, f"Categoria “{categoria.nome}” excluída. Os itens dela ficaram sem categoria.")
    categoria.delete()
    return redirect("financas:categorias")


# ---------- Análise ----------

def _mes_param(request, padrao):
    try:
        ano, mes = map(int, request.GET["mes"].split("-"))
        return date(ano, mes, 1)
    except (KeyError, ValueError):
        return padrao


def _ir_para_analise(conta, ancora="", mes=None):
    url = reverse("financas:analise", args=[conta.pk])
    if mes:
        url += f"?mes={mes:%Y-%m}"
    return redirect(url + (f"#{ancora}" if ancora else ""))


@com_familia
def analise(request, pk):
    """Fechamento do mês, para onde vai o dinheiro, poupança, alertas e o que fazer."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    hoje = date.today()
    atual = hoje.replace(day=1)
    categorias = {c.pk: c for c in request.familia.lista_categorias()}

    # fechamento: o último mês encerrado; sem nenhum ainda, o mês atual (parcial)
    fechados = insights.meses_fechados(conta, hoje, 1)
    mes = _mes_param(request, fechados[0] if fechados else atual)
    mes = min(max(mes, conta.data_inicio.replace(day=1)), atual)

    medias = insights.media_por_chave(conta, insights.meses_fechados(conta, hoje, 3), hoje)
    cats_atual = insights.categorias_do_mes(conta, atual, hoje, categorias, medias)
    objetivos = [insights.projecao_objetivo(o, conta, hoje) for o in conta.objetivos.prefetch_related("aportes")]
    return render(request, "financas/analise.html", {
        "conta": conta, "hoje": hoje, "atual": atual,
        "alertas": insights.alertas(conta, hoje, categorias, cats_atual),
        "fechamento": insights.fechamento(conta, mes, hoje, categorias),
        "mes_anterior": insights.somar_meses(mes, -1) if mes > conta.data_inicio.replace(day=1) else None,
        "mes_seguinte": insights.somar_meses(mes, 1) if mes < atual else None,
        "categorias_atual": cats_atual,
        "meses_media": len(insights.meses_fechados(conta, hoje, 3)),
        "ritmo": insights.ritmo_dia_a_dia(conta, hoje),
        "poupanca": insights.historico_poupanca(conta, hoje),
        "objetivos": objetivos,
        "cartao": insights.compromisso_cartao(conta, hoje),
        "assinaturas": insights.assinaturas_anuais(conta, hoje),
        "liberado": insights.liberacoes(conta, hoje),
        "variaveis": insights.contas_variaveis_acima(conta),
        "vazamento": insights.vazamento(conta, hoje),
        "sem_categoria": insights.sem_categoria(conta, hoje),
        "lista_categorias": list(categorias.values()),
        "uso_registrado": insights.uso_registrado(conta),
    })


def _objetivo_padrao(conta, escolhido=None):
    return escolhido or conta.objetivos.filter(padrao=True).first()


@require_POST
@com_familia
def guardar_sobra(request, pk):
    """Guarda como avulso o que sobrou num mês encerrado (o valor vem do cálculo, não do navegador)."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    hoje = date.today()
    try:
        mes = date.fromisoformat(request.POST.get("mes", "")).replace(day=1)
    except ValueError:
        return _ir_para_analise(conta)
    categorias = {c.pk: c for c in request.familia.lista_categorias()}
    f = insights.fechamento(conta, mes, hoje, categorias)
    if not f or not f["encerrado"] or f["sobra"] <= 0 or f["ja_guardou_sobra"]:
        messages.error(request, "Não há sobra para guardar neste mês.")
        return _ir_para_analise(conta, "fechamento", mes)
    objetivo = conta.objetivos.filter(pk=request.POST.get("objetivo") or 0).first()
    Aporte.objects.create(conta=conta, tipo=Aporte.AVULSO, data=max(hoje, conta.data_inicio), valor=f["sobra"],
                          descricao=f"Sobra de {mes:%m/%Y}", sobra_de=mes, objetivo=_objetivo_padrao(conta, objetivo))
    messages.success(request, f"{brl(f['sobra'])} da sobra de {mes:%m/%Y} guardados hoje.")
    return _ir_para_analise(conta, "fechamento", mes)


@require_POST
@com_familia
def liberado_decidir(request, pk):
    """Dinheiro liberado: somar ao guardado mensal ou só marcar como visto."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    hoje = date.today()
    lib = insights.liberacoes(conta, hoje)
    if request.POST.get("acao") == "somar" and lib["total_ja"] > 0:
        if conta.guardar_modo == Conta.GUARDAR_PERCENTUAL:
            messages.error(request, "O guardado está em % do salário. Ajuste a porcentagem em Configurar.")
            return _ir_para_analise(conta, "liberado")
        conta.guardar_modo = Conta.GUARDAR_FIXO
        conta.guardar_valor = (conta.guardar_valor or Decimal("0")) + lib["total_ja"]
        messages.success(request, f"Guardado mensal aumentado em {brl(lib['total_ja'])}: agora "
                                  f"{brl(conta.guardar_valor)} por mês.")
    conta.liberado_visto_ate = hoje.replace(day=1)
    conta.save(update_fields=["guardar_modo", "guardar_valor", "liberado_visto_ate"])
    return _ir_para_analise(conta, "liberado")


@require_POST
@com_familia
def assinatura_revisar(request, assinatura_pk):
    """"Ainda uso": a assinatura sai da lista de revisão por mais 6 meses."""
    a = get_object_or_404(AssinaturaCartao, pk=assinatura_pk, cartao__conta__familia=request.familia)
    a.revisada_em = date.today()
    a.save(update_fields=["revisada_em"])
    messages.success(request, f"{a.descricao} revisada. Volta a aparecer daqui a 6 meses.")
    return _ir_para_analise(a.cartao.conta, "assinaturas")


TIPOS_CLASSIFICAVEIS = {
    "gasto": (GastoDia, "conta", "descricao"),
    "lancamento": (Lancamento, "conta", "descricao"),
    "compra": (CompraCartao, "cartao__conta", "descricao"),
    "assinatura": (AssinaturaCartao, "cartao__conta", "descricao"),
    "fixa": (ContaFixa, "conta", "nome"),
}


@require_POST
@com_familia
def classificar(request, pk):
    """Define a categoria dos itens escolhidos e dos outros sem categoria com a mesma descrição."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    categorias = {str(c.pk): c for c in request.familia.lista_categorias()}
    total = 0
    with transaction.atomic():
        for campo, valor in request.POST.items():
            partes = campo.split("-")
            if len(partes) != 3 or partes[0] != "item" or partes[1] not in TIPOS_CLASSIFICAVEIS or valor not in categorias:
                continue
            Model, caminho, campo_desc = TIPOS_CLASSIFICAVEIS[partes[1]]
            item = Model.objects.filter(pk=partes[2], **{caminho: conta}).first()
            if item is None:
                continue
            item.categoria = categorias[valor]
            item.save(update_fields=["categoria"])
            total += 1
            for M, cam, cd in TIPOS_CLASSIFICAVEIS.values():
                total += M.objects.filter(**{cam: conta, f"{cd}__iexact": getattr(item, campo_desc),
                                             "categoria__isnull": True}).update(categoria=categorias[valor])
    if total:
        messages.success(request, f"{total} ite{'m classificado' if total == 1 else 'ns classificados'}.")
    return _ir_para_analise(conta, "classificar")


# ---------- Objetivos ----------

def _form_objetivo(request, conta, objetivo):
    form = ObjetivoForm(request.POST or None, instance=objetivo)
    if form.is_valid():
        with transaction.atomic():
            objetivo = form.save(commit=False)
            objetivo.conta = conta
            objetivo.save()
            if objetivo.padrao:
                conta.objetivos.exclude(pk=objetivo.pk).update(padrao=False)
        messages.success(request, f"Objetivo “{objetivo.nome}” salvo.")
        return _aba(conta, "investimentos")
    return render(request, "financas/objetivo_form.html", {
        "form": form, "conta": conta, "item": objetivo if objetivo.pk else None,
        "projecao": insights.projecao_objetivo(objetivo, conta, date.today()) if objetivo.pk else None,
    })


@com_familia
def objetivo_novo(request, pk):
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    primeiro = not conta.objetivos.exists()
    return _form_objetivo(request, conta, Objetivo(conta=conta, padrao=primeiro))


@com_familia
def objetivo_editar(request, objetivo_pk):
    objetivo = get_object_or_404(Objetivo, pk=objetivo_pk, conta__familia=request.familia)
    return _form_objetivo(request, objetivo.conta, objetivo)


@require_POST
@com_familia
def objetivo_excluir(request, objetivo_pk):
    objetivo = get_object_or_404(Objetivo, pk=objetivo_pk, conta__familia=request.familia)
    conta = objetivo.conta
    messages.success(request, f"Objetivo “{objetivo.nome}” excluído. O dinheiro guardado continua contado em Investimentos.")
    objetivo.delete()
    return _aba(conta, "investimentos")


# ---------- Importar extrato ----------

def _chave_importacao(conta):
    return f"importacao_{conta.pk}"


@com_familia
def importar(request, pk):
    """Envia o arquivo OFX/CSV; as transações ficam na sessão até a revisão ser confirmada."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    erro = None
    if request.method == "POST":
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            erro = "Escolha o arquivo do extrato."
        else:
            try:
                transacoes = importacao.ler_arquivo(arquivo.name, arquivo.read(importacao.TAMANHO_MAXIMO + 1))
            except importacao.ErroImportacao as e:
                erro = str(e)
            else:
                request.session[_chave_importacao(conta)] = [t.para_sessao() for t in transacoes]
                return redirect("financas:importar_revisar", pk=conta.pk)
    return render(request, "financas/importar.html", {"conta": conta, "erro": erro})


@com_familia
def importar_revisar(request, pk):
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    salvas = request.session.get(_chave_importacao(conta))
    if not salvas:
        return redirect("financas:importar", pk=conta.pk)
    hoje = date.today()
    transacoes = [importacao.Transacao.da_sessao(d) for d in salvas]
    categorias = {c.pk: c for c in request.familia.lista_categorias()}

    if request.method == "POST":
        if "cancelar" in request.POST:
            del request.session[_chave_importacao(conta)]
            return redirect("financas:painel", pk=conta.pk)
        importacao.sugerir(conta, transacoes, hoje)   # só para saber as ações válidas de cada uma
        escolhas = {}
        for i, t in enumerate(transacoes):
            acao = request.POST.get(f"acao-{i}", "ignorar")
            if acao not in {valor for valor, _ in t.opcoes}:
                acao = "ignorar"
            try:
                categoria_id = int(request.POST.get(f"categoria-{i}") or 0) or None
            except ValueError:
                categoria_id = None
            escolhas[i] = (acao, categoria_id)
        with transaction.atomic():
            resumo = importacao.aplicar(conta, transacoes, escolhas, categorias, hoje)
        del request.session[_chave_importacao(conta)]
        nomes = {"gasto": "gastos do dia", "saida": "saídas", "entrada": "entradas", "guardado": "guardados avulsos",
                 "confirmar": "meses de guardado confirmados", "pagar": "pagamentos de contas"}
        partes = [f"{n} {nomes[k]}" for k, n in resumo.items()]
        messages.success(request, ("Importado: " + ", ".join(partes) + ".") if partes else "Nada foi importado.")
        return redirect("financas:painel", pk=conta.pk)

    importacao.sugerir(conta, transacoes, hoje)
    return render(request, "financas/importar_revisar.html", {
        "conta": conta, "transacoes": transacoes, "categorias": list(categorias.values()),
        "total": len(transacoes), "a_importar": sum(1 for t in transacoes if t.acao != "ignorar"),
        "periodo": (transacoes[0].data, transacoes[-1].data),
    })
