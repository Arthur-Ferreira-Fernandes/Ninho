import calendar
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .calculos import (alerta_saldo, aplicar_escala, totais_do_mes, eventos_fixa, cronograma, extrato_mes, faturas, meses_do_seletor, proximas_fixas,
                       dia_de_guardar, guardados, proximo_fechamento, saldo_esperado, serie_diaria,
                       simular_mes)
from .forms import (AporteForm, GastoDiaForm, AssinaturaForm, PagamentoContaForm, CartaoForm, CompraCartaoForm, ConferenciaForm, ContaFixaForm, ContaForm, EntrarFamiliaForm, FamiliaForm,
                    LancamentoForm, PagamentosSalarioFormSet, UsoDiarioForm, UsuarioForm)
from .templatetags.dinheiro import brl
from .models import (Aporte, GastoDia, AssinaturaCartao, PagamentoConta, Cartao, CompraCartao, Conferencia, Conta, ContaFixa, Lancamento, Membro, PedidoEntrada, UsoDiario,
                     gerar_codigo_convite)

# modelo da URL -> (Model, Form, título)
MODELOS = {
    "fixa": (ContaFixa, ContaFixaForm, "Conta fixa"),
    "lancamento": (Lancamento, LancamentoForm, "Lançamento avulso"),
    "conferencia": (Conferencia, ConferenciaForm, "Conferir saldo real"),
}

# telas com layout próprio; as demais usam o formulário genérico
TEMPLATES_ITEM = {
    "fixa": "financas/conta_fixa_form.html",
    "lancamento": "financas/lancamento_form.html",
}


def _contexto_item(modelo, conta, form, item=None):
    """Dados extras para as telas com layout próprio."""
    if modelo == "fixa":
        if item is None or not item.pk:
            return {}
        ultimos = list(item.pagamentos.all()[:3])
        if not ultimos:
            return {}
        media = (sum((p.valor for p in ultimos), Decimal("0")) / len(ultimos)).quantize(Decimal("0.01"))
        return {"media_pagamentos": media, "ultimos_pagamentos": ultimos}
    if modelo != "lancamento":
        return {}
    if item is not None:
        # a validação do form altera a instância em memória; usa o que está salvo
        item = Lancamento.objects.get(pk=item.pk)
    data = form.initial.get("data") if item is None else item.data
    if not isinstance(data, date) or data < conta.data_inicio:
        return {}
    # saldo esperado do dia sem este lançamento, para o resumo mostrar o efeito
    base = saldo_esperado(conta, data).esperado
    if item is not None:
        base += -item.valor if item.tipo == Lancamento.ENTRADA else item.valor
    return {"base_data": data, "base_esperado": base}


def _modelo(nome):
    try:
        return MODELOS[nome]
    except KeyError:
        raise Http404


def com_familia(view):
    """Exige login e família; disponibiliza request.membro e request.familia."""
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        membro = getattr(request.user, "membro", None)
        if membro is None:
            return redirect("financas:sem_familia")
        request.membro = membro
        request.familia = membro.familia
        return view(request, *args, **kwargs)
    return wrapper


def so_admin(view):
    @com_familia
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.membro.is_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


# ---------- Cadastro e família ----------

def cadastro(request):
    if request.user.is_authenticated:
        return redirect("financas:inicio")
    form = UsuarioForm(request.POST or None)
    if form.is_valid():
        login(request, form.save())
        return redirect("financas:sem_familia")
    return render(request, "financas/cadastro.html", {"form": form})


@login_required
def sem_familia(request):
    """Para quem ainda não tem família: criar uma ou pedir para entrar com um código."""
    if hasattr(request.user, "membro"):
        return redirect("financas:inicio")
    pedido = PedidoEntrada.objects.filter(usuario=request.user).select_related("familia").first()

    form_criar = FamiliaForm(prefix="criar")
    form_entrar = EntrarFamiliaForm(prefix="entrar")
    if request.method == "POST" and pedido is None:
        if "criar" in request.POST:
            form_criar = FamiliaForm(request.POST, prefix="criar")
            if form_criar.is_valid():
                with transaction.atomic():
                    familia = form_criar.save()
                    Membro.objects.create(familia=familia, usuario=request.user, papel=Membro.ADMIN)
                return redirect("financas:inicio")
        else:
            form_entrar = EntrarFamiliaForm(request.POST, prefix="entrar")
            if form_entrar.is_valid():
                PedidoEntrada.objects.create(familia=form_entrar.familia, usuario=request.user,
                                             parentesco=form_entrar.cleaned_data["parentesco"])
                return redirect("financas:sem_familia")

    return render(request, "financas/sem_familia.html", {
        "pedido": pedido, "form_criar": form_criar, "form_entrar": form_entrar,
    })


@require_POST
@login_required
def pedido_cancelar(request):
    PedidoEntrada.objects.filter(usuario=request.user).delete()
    return redirect("financas:sem_familia")


@com_familia
def familia(request):
    pode_editar = request.membro.is_admin
    form_familia = FamiliaForm(instance=request.familia)
    if request.method == "POST":
        if not pode_editar:
            raise PermissionDenied
        form_familia = FamiliaForm(request.POST, instance=request.familia)
        if form_familia.is_valid():
            form_familia.save()
            messages.success(request, "Nome da família atualizado.")
            return redirect("financas:familia")
    return render(request, "financas/familia.html", {
        "familia": request.familia,
        "membros": request.familia.membros.select_related("usuario"),
        "pedidos": request.familia.pedidos.select_related("usuario") if pode_editar else [],
        "pode_editar": pode_editar,
        "form_familia": form_familia,
    })


@require_POST
@so_admin
def codigo_novo(request):
    """Gera outro código de convite; o antigo deixa de funcionar."""
    request.familia.codigo_convite = gerar_codigo_convite()
    request.familia.save()
    messages.success(request, "Novo código gerado. O anterior não funciona mais.")
    return redirect("financas:familia")


@require_POST
@so_admin
def pedido_responder(request, pedido_pk, aceitar):
    pedido = get_object_or_404(PedidoEntrada, pk=pedido_pk, familia=request.familia)
    nome = pedido.usuario.get_full_name() or pedido.usuario.username
    with transaction.atomic():
        if aceitar:
            Membro.objects.create(familia=pedido.familia, usuario=pedido.usuario,
                                  parentesco=pedido.parentesco)
        pedido.delete()
    if aceitar:
        messages.success(request, f"{nome} agora faz parte da família.")
    else:
        messages.success(request, f"Pedido de {nome} recusado.")
    return redirect("financas:familia")


@require_POST
@so_admin
def membro_alternar_admin(request, membro_pk):
    membro = get_object_or_404(Membro, pk=membro_pk, familia=request.familia)
    if membro == request.membro:
        messages.error(request, "Você não pode mudar o seu próprio papel.")
    else:
        membro.papel = Membro.MEMBRO if membro.is_admin else Membro.ADMIN
        membro.save()
    return redirect("financas:familia")


@require_POST
@so_admin
def membro_remover(request, membro_pk):
    membro = get_object_or_404(Membro, pk=membro_pk, familia=request.familia)
    if membro == request.membro:
        messages.error(request, "Você não pode remover a si mesmo(a).")
    else:
        messages.success(request, f"{membro} foi removido(a) da família.")
        membro.delete()
    return redirect("financas:familia")


# ---------- Contas ----------

@com_familia
def inicio(request):
    contas = request.familia.contas.all()
    if contas.count() == 1:
        return redirect("financas:painel", pk=contas.first().pk)
    if not contas:
        return redirect("financas:conta_nova")
    return render(request, "financas/inicio.html", {"contas": contas})


def _form_conta(request, conta):
    """Processa o formulário da conta junto com os pagamentos do salário."""
    dados = request.POST or None
    # sugestões só para conta nova: em ModelForm, "initial" se sobrepõe aos dados salvos
    inicial = {} if conta.pk else {"data_inicio": date.today(), "modo_gasto": Conta.PERCENTUAL}
    form = ContaForm(dados, instance=conta, initial=inicial)
    exigir = bool(dados) and dados.get("modo_gasto") == Conta.PERCENTUAL
    pagamentos = PagamentosSalarioFormSet(dados, instance=conta, prefix="salario", exigir=exigir)

    if dados and form.is_valid() and pagamentos.is_valid():
        with transaction.atomic():
            conta = form.save(commit=False)
            if not conta.pk:
                conta.familia = request.familia
            conta.save()
            pagamentos.instance = conta
            pagamentos.save()
        return redirect("financas:painel", pk=conta.pk)

    primeira = conta.pk is None and not request.familia.contas.exists()
    return render(request, "financas/conta_form.html", {
        "form": form,
        "pagamentos": pagamentos,
        "conta": conta if conta.pk else None,
        # a primeira conta é o último passo do cadastro, no layout de boas-vindas
        "base_template": "financas/auth_base.html" if primeira else "financas/base.html",
        "primeira": primeira,
    })


@com_familia
def conta_nova(request):
    return _form_conta(request, Conta(familia=request.familia))


@com_familia
def conta_editar(request, pk):
    return _form_conta(request, get_object_or_404(Conta, pk=pk, familia=request.familia))


def _data_param(origem, nome):
    """Lê uma data AAAA-MM-DD de request.GET (ou de um QueryDict passado direto)."""
    params = getattr(origem, "GET", origem)
    try:
        return date.fromisoformat(params.get(nome, ""))
    except ValueError:
        return None


def _proximos_vencimentos(conta, hoje, quantidade=8):
    """Contas fixas e faturas de cartão a partir de amanhã: [(data, item, dias que faltam)]."""
    amanha = hoje + timedelta(days=1)
    itens = proximas_fixas(conta, hoje, quantidade)
    itens += [(f.vencimento, f) for f in faturas(conta, amanha, hoje + timedelta(days=400))][:quantidade]
    itens.sort(key=lambda x: x[0])
    return [(d, item, (d - hoje).days) for d, item in itens[:quantidade]]


def _cartoes_com_proxima_fatura(conta, hoje):
    """Para cada cartão: a próxima fatura a vencer e o resumo do próximo fechamento."""
    lista_faturas = faturas(conta, hoje, hoje + timedelta(days=100))
    resultado = []
    for c in conta.cartoes.all():
        do_cartao = [f for f in lista_faturas if f.cartao.pk == c.pk]
        fechamento, vencimento = proximo_fechamento(c, hoje)
        fatura = next((f for f in do_cartao if f.fechamento == fechamento), None)
        calcula = fechamento >= conta.data_inicio
        assinaturas = [a for a in c.assinaturas.all() if a.ativa_em(hoje) or a.inicio > hoje]
        resultado.append({
            "cartao": c,
            "proxima": do_cartao[0] if do_cartao else None,
            "fechamento": {
                "data": fechamento,
                "vencimento": vencimento,
                "fatura": fatura.total if fatura else Decimal("0"),
                "saldo": saldo_esperado(conta, fechamento).esperado if calcula else None,
                "saldo_apos": saldo_esperado(conta, vencimento).esperado if calcula else None,
            },
            "assinaturas_mes": sum((a.valor for a in assinaturas if a.ativa_em(hoje)), Decimal("0")),
        })
    return resultado


def _investimentos(conta, hoje, meses_a_frente=12):
    """Histórico e previsão do que foi guardado (meses e avulsos), com o acumulado."""
    if conta.guardar_modo == Conta.GUARDAR_NADA and not conta.aportes.exists():
        return None
    ano_fim, mes_fim = divmod(hoje.year * 12 + hoje.month - 1 + meses_a_frente, 12)
    fim = date(ano_fim, mes_fim + 1, calendar.monthrange(ano_fim, mes_fim + 1)[1])
    linhas, acumulado = [], Decimal("0")
    for g in guardados(conta, conta.data_inicio, fim):
        acumulado += g.valor
        referencia = g.aporte.referencia if (g.aporte and not g.avulso) else g.data.replace(day=1)
        if g.avulso:
            situacao = "agendado" if g.data > hoje else "avulso"
        elif g.confirmado:
            situacao = "confirmado"
        elif referencia > hoje.replace(day=1):
            situacao = "previsto"
        else:
            situacao = "pendente"      # mês que já começou e ainda não foi confirmado
        linhas.append({"g": g, "data": g.data, "valor": g.valor, "acumulado": acumulado, "situacao": situacao,
                       "referencia": referencia, "atual": referencia == hoje.replace(day=1) and not g.avulso})
    ate_hoje = [l for l in linhas if l["data"] <= hoje]
    mensais_passados = [l for l in linhas if not l["g"].avulso and l["referencia"] <= hoje.replace(day=1)]
    return {
        "mensal": conta.guardar_mensal,
        "linhas": linhas,
        "total_hoje": ate_hoje[-1]["acumulado"] if ate_hoje else Decimal("0"),
        "total_12": linhas[-1]["acumulado"] if linhas else Decimal("0"),
        "confirmados": sum(1 for l in mensais_passados if l["situacao"] == "confirmado"),
        "pendentes": sum(1 for l in mensais_passados if l["situacao"] == "pendente"),
        "avulsos": sum((l["valor"] for l in linhas if l["g"].avulso and l["data"] <= hoje), Decimal("0")),
        "proximo": next((l for l in linhas if l["data"] > hoje and not l["g"].avulso), None),
    }


def _guardar_info(conta, hoje):
    """Quanto se guarda por mês, em que dia deste mês e quanto já foi guardado."""
    if conta.guardar_mensal <= 0:
        return None
    este_mes = dia_de_guardar(conta, hoje.year, hoje.month)
    proximo_ano, proximo_mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
    return {
        "mensal": conta.guardar_mensal,
        "este_mes": este_mes,
        "proximo": dia_de_guardar(conta, proximo_ano, proximo_mes),
        "total": sum((g.valor for g in guardados(conta, conta.data_inicio, hoje)), Decimal("0")),
    }


def _dia_a_dia_do_mes(conta, hoje):
    """Dinheiro do dia a dia no mês atual: orçamento, quanto já foi usado e quanto resta."""
    dias = simular_mes(conta, hoje.year, hoje.month)
    if not dias:
        return None
    usado = sum((d.gasto for d in dias if d.data <= hoje), Decimal("0"))
    # orçamento do mês (proporcional se o cálculo começou no meio dele)
    total = (conta.orcamento_mensal * len(dias) / calendar.monthrange(hoje.year, hoje.month)[1]).quantize(Decimal("0.01"))
    hoje_dia = next((d for d in dias if d.data == hoje), None)
    return {
        "mensal": conta.orcamento_mensal,
        "mes_total": total,
        "usado": usado,
        "restante": total - usado,
        "pct": min(100, round(usado * 100 / total)) if total else 0,
        "hoje": hoje_dia.disponivel if hoje_dia else None,
        "proporcional": len(dias) < calendar.monthrange(hoje.year, hoje.month)[1],
    }


def _situacao_periodica(fixa, hoje):
    """Para contas a cada X dias: a próxima ocorrência ainda não marcada e a última marcada."""
    intervalo = fixa.intervalo_dias or 30
    eventos = eventos_fixa(fixa, hoje - timedelta(days=intervalo * 3), hoje + timedelta(days=intervalo * 3))
    pendente = next((e for e in eventos if e.pagamento is None), None)
    ultima = max(fixa.pagamentos.all(), key=lambda p: p.data, default=None)
    return {"proxima_evento": pendente, "ultima_compra": ultima,
            "dia": pendente.data.day if pendente else fixa.dia}


def _situacao_do_mes(fixa, hoje):
    """Para contas variáveis: vencimento deste mês e o pagamento dele, se houver."""
    vencimento = date(hoje.year, hoje.month, min(fixa.dia, calendar.monthrange(hoje.year, hoje.month)[1]))
    pagamento = next((p for p in fixa.pagamentos.all()
                      if (p.referencia.year, p.referencia.month) == (hoje.year, hoje.month)), None)
    return {"vencimento_mes": vencimento, "pagamento_mes": pagamento}


def _recorrentes(fixas, assinaturas, hoje):
    """Contas fixas e assinaturas do cartão numa lista só, pelo dia do mês.

    As inativas (pausadas ou canceladas) vão para o fim.
    """
    itens = [{"tipo": "fixa", "obj": f, "dia": f.dia, "nome": f.nome, "valor": f.valor,
              "receita": f.tipo == ContaFixa.RECEITA,
              "inativa": not f.ativa or (f.vigente_ate is not None and f.vigente_ate < hoje),
              "futura": f.vigente_desde is not None and f.vigente_desde > hoje,
              **(_situacao_periodica(f, hoje) if f.periodica else
                 _situacao_do_mes(f, hoje) if f.variavel else {})} for f in fixas]
    itens += [{"tipo": "assinatura", "obj": a, "dia": a.dia_cobranca, "nome": a.descricao, "valor": a.valor,
               "receita": False, "inativa": a.encerrada} for a in assinaturas]
    itens.sort(key=lambda i: (i["inativa"], i["dia"], i["nome"].lower()))
    return itens


def _assinaturas(conta, hoje):
    lista = list(AssinaturaCartao.objects.filter(cartao__conta=conta).select_related("cartao"))
    for a in lista:
        a.encerrada = a.fim is not None and a.fim <= hoje
    lista.sort(key=lambda a: (a.encerrada, a.descricao.lower()))
    return {
        "lista": lista,
        "total_mes": sum((a.valor for a in lista if a.ativa_em(hoje)), Decimal("0")),
    }


def _compras_com_andamento(conta, hoje):
    """Compras no cartão com quantas parcelas já venceram; as em andamento primeiro."""
    lista = []
    for compra in CompraCartao.objects.filter(cartao__conta=conta).select_related("cartao"):
        parcelas = cronograma(compra)
        pagas = sum(1 for p in parcelas if p.vencimento <= hoje)
        proxima = next((p for p in parcelas if p.vencimento > hoje), None)
        lista.append({"compra": compra, "pagas": pagas, "proxima": proxima,
                      "restante": sum((p.valor for p in parcelas if p.vencimento > hoje), Decimal("0")),
                      "progresso": round(pagas * 100 / compra.parcelas)})
    lista.sort(key=lambda c: (c["proxima"] is None, c["proxima"].vencimento if c["proxima"] else date.min))
    return lista


def _historico_meses(conta, hoje, mes_aberto, dias_mes_aberto, antes=6, depois=2):
    """Recebido − gasto dos últimos meses, do atual e dos próximos (previsão)."""
    atual = hoje.replace(day=1)
    linhas = []
    for k in range(-antes, depois + 1):
        ano, mes = divmod(atual.year * 12 + atual.month - 1 + k, 12)
        primeiro = date(ano, mes + 1, 1)
        if date(ano, mes + 1, calendar.monthrange(ano, mes + 1)[1]) < conta.data_inicio:
            continue
        # o mês aberto no extrato já foi calculado
        dias = dias_mes_aberto if primeiro == mes_aberto else extrato_mes(conta, primeiro.year, primeiro.month)
        t = totais_do_mes(dias)
        if t:
            linhas.append({"mes": primeiro, "atual": primeiro == atual, "previsao": primeiro > atual,
                           "aberto": primeiro == mes_aberto, **t})
    return linhas


def _resumo_mes(ativos, hoje):
    """Como o mês foi (ou deve ser): saldo no começo e no fim, o menor saldo e dias no negativo."""
    if not ativos:
        return None
    menor = min(ativos, key=lambda d: d.esperado)
    return {
        "inicio": ativos[0].inicio,
        "fim": ativos[-1].esperado,
        "variacao": ativos[-1].esperado - ativos[0].inicio,
        "menor": menor,
        "dias_negativos": sum(1 for d in ativos if d.esperado < 0),
        "dias": len(ativos),
        "encerrado": ativos[-1].data < hoje,
    }


@com_familia
def painel(request, pk):
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    hoje = date.today()

    # ?dia=AAAA-MM-DD escolhe o dia; ?mes=AAAA-MM navega pelos meses
    selecionado = _data_param(request, "dia") or _data_param(request, "data")
    try:
        ano, mes = map(int, request.GET["mes"].split("-"))
        primeiro = date(ano, mes, 1)
    except (KeyError, ValueError):
        primeiro = (selecionado or hoje).replace(day=1)
    if not selecionado or (selecionado.year, selecionado.month) != (primeiro.year, primeiro.month):
        # mês atual: hoje; mês passado: o último dia (fechamento); mês futuro: o dia 1
        ultimo = date(primeiro.year, primeiro.month, calendar.monthrange(primeiro.year, primeiro.month)[1])
        if primeiro <= hoje <= ultimo:
            selecionado = hoje
        elif ultimo < hoje:
            selecionado = ultimo
        else:
            selecionado = primeiro

    dias = extrato_mes(conta, primeiro.year, primeiro.month)
    legenda = aplicar_escala(dias, conta)
    # calendário do mapa: semanas de segunda a domingo
    vazios_inicio = range(primeiro.weekday())
    dia = dias[selecionado.day - 1]
    ativos = [d for d in dias if d.ativo]

    conferencias = []
    # a conferência da véspera do início é a que originou o ponto de partida
    periodo = conta.conferencias.filter(data__gte=conta.data_inicio - timedelta(days=1))
    for c in periodo[:50]:
        esperado = saldo_esperado(conta, c.data).esperado
        conferencias.append({"obj": c, "esperado": esperado, "diferenca": c.saldo_real - esperado})

    fixas = list(conta.fixas.prefetch_related("pagamentos"))
    # totais do mês: só o que está valendo agora (salário novo, aluguel encerrado...)
    ativas = [f for f in fixas if f.ativa and f.vigente_em(hoje if f.periodica else
                                                          f.ocorrencia_no_mes(hoje.year, hoje.month))]
    assinaturas = _assinaturas(conta, hoje)
    receitas_fixas = sum((f.valor_mensal for f in ativas if f.tipo == ContaFixa.RECEITA), Decimal("0"))
    despesas_fixas = sum((f.valor_mensal for f in ativas if f.tipo == ContaFixa.DESPESA), Decimal("0"))

    anterior = primeiro - timedelta(days=1)
    seguinte = primeiro + timedelta(days=32)
    return render(request, "financas/painel.html", {
        "conta": conta,
        "hoje": hoje,
        "dias": dias,
        "legenda": legenda,
        "alerta": alerta_saldo(conta, hoje),
        "vazios_inicio": vazios_inicio,
        "resumo_mes": _resumo_mes(ativos, hoje),
        "totais_mes": totais_do_mes(dias),
        "historico_meses": _historico_meses(conta, hoje, primeiro, dias),
        "pode_voltar": primeiro > conta.data_inicio.replace(day=1),
        "anos_seletor": meses_do_seletor(
            conta, min(conta.data_inicio.year, primeiro.year), max(hoje.year + 1, primeiro.year), hoje),
        "dia": dia,
        "resumo": saldo_esperado(conta, selecionado),
        "mes": primeiro,
        "diario_mes": conta.gasto_diario_em(primeiro),
        "mes_anterior": anterior.strftime("%Y-%m"),
        "mes_seguinte": seguinte.strftime("%Y-%m"),
        "mes_hoje": hoje.strftime("%Y-%m"),
        "dia_anterior": selecionado - timedelta(days=1),
        "dia_seguinte": selecionado + timedelta(days=1),
        "totais": {
            "entradas": sum(d.entradas for d in ativos),
            "saidas": sum(d.saidas for d in ativos),
            "gasto": sum(d.gasto for d in ativos),
        },
        "ultima": conferencias[0] if conferencias else None,
        "conferencias": conferencias,
        "fixas": fixas,
        "recorrentes": _recorrentes(fixas, assinaturas["lista"], hoje),
        "dia_a_dia": _dia_a_dia_do_mes(conta, hoje),
        "guardar": _guardar_info(conta, hoje),
        "investimentos": _investimentos(conta, hoje),
        "totais_fixas": {
            "receitas": receitas_fixas,
            "despesas": despesas_fixas + assinaturas["total_mes"],
            "assinaturas": assinaturas["total_mes"],
            "dia_a_dia": conta.orcamento_mensal,
            "guardar": conta.guardar_mensal,
            "saidas": despesas_fixas + assinaturas["total_mes"] + conta.orcamento_mensal + conta.guardar_mensal,
            "sobra": (receitas_fixas - despesas_fixas - assinaturas["total_mes"] - conta.orcamento_mensal
                      - conta.guardar_mensal),
        },
        "lancamentos": conta.lancamentos.all()[:30],
        "proximas": _proximos_vencimentos(conta, hoje),
        "cartoes": _cartoes_com_proxima_fatura(conta, hoje),
        "compras": _compras_com_andamento(conta, hoje),
        "assinaturas": assinaturas,
        "form_conferencia": ConferenciaForm(initial={"data": selecionado}),
    })


@require_POST
@com_familia
def uso_diario(request, pk):
    """Registra se o dinheiro do dia foi usado (ou desfaz o registro)."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    data = _data_param(request.POST, "data")
    existente = conta.usos.filter(data=data).first() if data else None

    if request.POST.get("situacao") == "limpar":
        if existente:
            existente.delete()
    else:
        form = UsoDiarioForm(request.POST, instance=existente or UsoDiario(conta=conta),
                             conta=conta, hoje=date.today())
        if form.is_valid():
            form.save()
        else:
            for erros in form.errors.values():
                for erro in erros:
                    messages.error(request, erro)

    destino = reverse("financas:painel", args=[conta.pk])
    if data:
        destino += f"?mes={data:%Y-%m}&dia={data:%Y-%m-%d}"
    return redirect(destino + "#extrato")


def _destino_painel(modelo, item, voltar=None):
    """URL do painel para onde voltar depois de salvar, cancelar ou excluir um item.

    voltar="contas" ou "cartoes" volta para essa aba; voltar="dia:AAAA-MM-DD" volta para esse
    dia no extrato. Sem isso: contas fixas vão para a aba Contas e os demais itens
    para o dia deles no extrato.
    """
    url = reverse("financas:painel", args=[item.conta_id])
    if voltar and voltar.startswith("dia:"):
        try:
            d = date.fromisoformat(voltar[4:])
            return url + f"?mes={d:%Y-%m}&dia={d:%Y-%m-%d}#extrato"
        except ValueError:
            pass
    if voltar == "cartoes":
        return url + "#cartoes"
    if modelo == "fixa" or voltar == "contas":
        return url + "#contas"
    return url + f"?mes={item.data:%Y-%m}&dia={item.data:%Y-%m-%d}#extrato"


def _voltar_ao_painel(modelo, item, voltar=None):
    return redirect(_destino_painel(modelo, item, voltar))


# ---------- Encerrar (ou reabrir) contas fixas e assinaturas ----------

@require_POST
@com_familia
def fixa_encerrar(request, fixa_pk):
    """Define até quando a conta fixa vale (inclusive), ou tira a data de fim."""
    fixa = get_object_or_404(ContaFixa, pk=fixa_pk, conta__familia=request.familia)
    if request.POST.get("reabrir"):
        fixa.vigente_ate = None
        fixa.save(update_fields=["vigente_ate"])
        messages.success(request, f"{fixa.nome} voltou a valer sem data para acabar.")
        return _aba(fixa.conta, "contas")
    ate = _data_param(request.POST, "ate")
    if ate is None:
        messages.error(request, "Informe a data em que termina.")
    elif fixa.vigente_desde and ate < fixa.vigente_desde:
        messages.error(request, f"A data de fim não pode ser antes do início ({fixa.vigente_desde:%d/%m/%Y}).")
    else:
        fixa.vigente_ate = ate
        fixa.save(update_fields=["vigente_ate"])
        messages.success(request, f"{fixa.nome} termina em {ate:%d/%m/%Y}. Os meses anteriores continuam valendo.")
    return _aba(fixa.conta, "contas")


@require_POST
@com_familia
def assinatura_encerrar(request, assinatura_pk):
    """Cancela a assinatura a partir de uma data (não cobra mais nesse dia nem depois), ou reabre."""
    a = get_object_or_404(AssinaturaCartao, pk=assinatura_pk, cartao__conta__familia=request.familia)
    conta = a.cartao.conta
    if request.POST.get("reabrir"):
        a.fim = None
        a.save(update_fields=["fim"])
        messages.success(request, f"Assinatura {a.descricao} reativada.")
        return _aba(conta, "contas")
    fim = _data_param(request.POST, "fim")
    if fim is None:
        messages.error(request, "Informe a data do cancelamento.")
    elif fim < a.inicio:
        messages.error(request, f"O cancelamento não pode ser antes do início ({a.inicio:%d/%m/%Y}).")
    else:
        a.fim = fim
        a.save(update_fields=["fim"])
        messages.success(request, f"Assinatura {a.descricao} cancelada a partir de {fim:%d/%m/%Y}.")
    return _aba(conta, "contas")


# ---------- Gastos detalhados do dinheiro do dia ----------

def _voltar_ao_dia(conta, data):
    return redirect(reverse("financas:painel", args=[conta.pk]) + f"?mes={data:%Y-%m}&dia={data:%Y-%m-%d}#extrato")


@require_POST
@com_familia
def gasto_dia_adicionar(request, pk):
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    form = GastoDiaForm(request.POST, conta=conta, hoje=date.today(), instance=GastoDia(conta=conta))
    if form.is_valid():
        form.save()
    else:
        _mostrar_erros(request, form)
    data = _data_param(request.POST, "data") or date.today()
    return _voltar_ao_dia(conta, data)


@require_POST
@com_familia
def gasto_dia_excluir(request, gasto_pk):
    gasto = get_object_or_404(GastoDia, pk=gasto_pk, conta__familia=request.familia)
    conta, data = gasto.conta, gasto.data
    gasto.delete()
    return _voltar_ao_dia(conta, data)


# ---------- Investimentos: o que foi guardado de fato ----------

def _aba_investimentos(conta):
    return _aba(conta, "investimentos")


def _mostrar_erros(request, form):
    for erros in form.errors.values():
        for erro in erros:
            messages.error(request, erro)


@require_POST
@com_familia
def aporte_confirmar(request, pk):
    """Informa quanto foi guardado de fato num mês (substitui a previsão daquele mês)."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    referencia = _data_param(request.POST, "referencia")
    hoje = date.today()
    if referencia is None:
        raise Http404
    referencia = referencia.replace(day=1)
    if referencia > hoje.replace(day=1):
        messages.error(request, "Só dá para confirmar meses que já começaram.")
        return _aba_investimentos(conta)
    existente = conta.aportes.filter(tipo=Aporte.MENSAL, referencia=referencia).first()
    form = AporteForm(request.POST, conta=conta, hoje=hoje,
                      instance=existente or Aporte(conta=conta, tipo=Aporte.MENSAL, referencia=referencia))
    if form.is_valid():
        a = form.save()
        messages.success(request, f"Guardado de {referencia:%m/%Y} confirmado: {brl(a.valor)} em {a.data:%d/%m}.")
    else:
        _mostrar_erros(request, form)
    return _aba_investimentos(conta)


@require_POST
@com_familia
def aporte_avulso(request, pk):
    """Guarda um valor avulso em qualquer dia."""
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    form = AporteForm(request.POST, conta=conta, hoje=date.today(), instance=Aporte(conta=conta, tipo=Aporte.AVULSO))
    if form.is_valid():
        a = form.save()
        messages.success(request, f"{brl(a.valor)} guardados em {a.data:%d/%m/%Y}.")
    else:
        _mostrar_erros(request, form)
    return _aba_investimentos(conta)


@require_POST
@com_familia
def aporte_excluir(request, aporte_pk):
    aporte = get_object_or_404(Aporte, pk=aporte_pk, conta__familia=request.familia)
    conta = aporte.conta
    if aporte.tipo == Aporte.MENSAL:
        messages.success(request, f"Confirmação de {aporte.referencia:%m/%Y} desfeita; volta a valer a previsão.")
    else:
        messages.success(request, f"Valor avulso de {brl(aporte.valor)} em {aporte.data:%d/%m} excluído.")
    aporte.delete()
    return _aba_investimentos(conta)


# ---------- Contas variáveis: marcar como paga ----------

@require_POST
@com_familia
def pagamento_registrar(request, fixa_pk):
    """Marca a ocorrência (mês) de uma conta variável como paga, com o valor real."""
    fixa = get_object_or_404(ContaFixa, pk=fixa_pk, conta__familia=request.familia)
    referencia = _data_param(request.POST, "referencia")
    if referencia is None:
        raise Http404
    if fixa.periodica:
        # a cada X dias: uma marcação por ocorrência (pode haver mais de uma no mês)
        existente = fixa.pagamentos.filter(referencia=referencia).first()
    else:
        existente = fixa.pagamentos.filter(referencia__year=referencia.year,
                                           referencia__month=referencia.month).first()
    form = PagamentoContaForm(request.POST, instance=existente or PagamentoConta(fixa=fixa, referencia=referencia))
    if form.is_valid():
        p = form.save()
        verbo = "recebida" if fixa.tipo == ContaFixa.RECEITA else "paga"
        if not fixa.marcavel:
            messages.success(request, f"{fixa.nome} de {referencia:%m/%Y} ajustado só neste mês: "
                                      f"{brl(p.valor)} em {p.data:%d/%m}. Os outros meses continuam com {brl(fixa.valor)}.")
        elif fixa.periodica:
            proxima = p.data + timedelta(days=fixa.intervalo_dias)
            messages.success(request, f"{fixa.nome}: {brl(p.valor)} em {p.data:%d/%m}. "
                                      f"Próxima prevista para {proxima:%d/%m/%Y}.")
        else:
            messages.success(request, f"{fixa.nome} de {referencia:%m/%Y} marcada como {verbo}: {brl(p.valor)}.")
    else:
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
    return _voltar_ao_painel("fixa", fixa, request.POST.get("voltar") or f"dia:{referencia.isoformat()}")


@require_POST
@com_familia
def pagamento_excluir(request, pagamento_pk):
    pagamento = get_object_or_404(PagamentoConta, pk=pagamento_pk, fixa__conta__familia=request.familia)
    fixa = pagamento.fixa
    if fixa.marcavel:
        messages.success(request, f"Pagamento de {fixa.nome} de {pagamento.referencia:%m/%Y} desfeito; "
                                  "volta a valer a estimativa.")
    else:
        messages.success(request, f"Ajuste de {fixa.nome} em {pagamento.referencia:%m/%Y} desfeito; "
                                  f"volta a valer {brl(fixa.valor)}.")
    pagamento.delete()
    return _voltar_ao_painel("fixa", fixa, request.POST.get("voltar"))


# ---------- Cartões e compras no crédito ----------

def _aba(conta, aba):
    return redirect(reverse("financas:painel", args=[conta.pk]) + "#" + aba)


@com_familia
def cartao_form(request, pk=None, cartao_pk=None):
    if cartao_pk:
        cartao = get_object_or_404(Cartao, pk=cartao_pk, conta__familia=request.familia)
        conta = cartao.conta
    else:
        conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
        cartao = Cartao(conta=conta)
    form = CartaoForm(request.POST or None, instance=cartao)
    if form.is_valid():
        cartao = form.save()
        proximo = {"compra": "financas:compra_nova", "assinatura": "financas:assinatura_nova"}
        if request.GET.get("proximo") in proximo:
            # veio de uma tela que precisa de cartão: volta para ela com o cartão escolhido
            destino = reverse(proximo[request.GET["proximo"]], args=[conta.pk])
            return redirect(destino + f"?cartao={cartao.pk}")
        return _aba(conta, "cartoes")
    return render(request, "financas/cartao_form.html",
                  {"form": form, "conta": conta, "item": cartao if cartao.pk else None})


@require_POST
@com_familia
def cartao_excluir(request, cartao_pk):
    cartao = get_object_or_404(Cartao, pk=cartao_pk, conta__familia=request.familia)
    messages.success(request, f"Cartão “{cartao.nome}” e suas compras foram excluídos.")
    cartao.delete()
    return _aba(cartao.conta, "cartoes")


@com_familia
def compra_form(request, pk=None, compra_pk=None):
    if compra_pk:
        compra = get_object_or_404(CompraCartao, pk=compra_pk, cartao__conta__familia=request.familia)
        conta = compra.cartao.conta
    else:
        conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
        if not conta.cartoes.exists():
            messages.info(request, "Cadastre o cartão primeiro; depois você volta para a compra.")
            return redirect(reverse("financas:cartao_novo", args=[conta.pk]) + "?proximo=compra")
        compra = CompraCartao()
    cartoes = list(conta.cartoes.all())
    # sugestões só para compra nova (em ModelForm, "initial" se sobrepõe aos dados salvos)
    inicial = {} if compra.pk else {"data_compra": _data_param(request, "data") or date.today(), "parcelas": 1,
                                    "cartao": request.GET.get("cartao") or cartoes[0].pk}
    form = CompraCartaoForm(request.POST or None, instance=compra, conta=conta, initial=inicial)
    if form.is_valid():
        compra = form.save()
        n = compra.parcelas
        messages.success(request, f"“{compra.descricao}” lançada em {n} parcela{'s' if n > 1 else ''} "
                                  f"no {compra.cartao.nome}.")
        return _aba(conta, "cartoes")
    return render(request, "financas/compra_form.html", {
        "form": form, "conta": conta, "cartoes": cartoes,
        "cartoes_dados": [{"id": c.pk, "nome": c.nome, "fechamento": c.dia_fechamento,
                           "vencimento": c.dia_vencimento} for c in cartoes],
        "inicio_calculo": conta.data_inicio.isoformat(),
        "projecao": _projecao_sem_compra(conta, compra),
        "item": compra if compra.pk else None,
    })


@com_familia
def assinatura_form(request, pk=None, assinatura_pk=None):
    if assinatura_pk:
        assinatura = get_object_or_404(AssinaturaCartao, pk=assinatura_pk,
                                       cartao__conta__familia=request.familia)
        conta = assinatura.cartao.conta
    else:
        conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
        if not conta.cartoes.exists():
            messages.info(request, "Cadastre o cartão primeiro; depois você volta para a assinatura.")
            return redirect(reverse("financas:cartao_novo", args=[conta.pk]) + "?proximo=assinatura")
        assinatura = AssinaturaCartao()
    cartoes = list(conta.cartoes.all())
    # sugestões só para assinatura nova (em ModelForm, "initial" se sobrepõe aos dados salvos)
    inicial = {} if assinatura.pk else {"inicio": date.today(), "dia_cobranca": date.today().day,
                                        "cartao": request.GET.get("cartao") or cartoes[0].pk}
    form = AssinaturaForm(request.POST or None, instance=assinatura, conta=conta, initial=inicial)
    if form.is_valid():
        assinatura = form.save()
        messages.success(request, f"Assinatura “{assinatura.descricao}” salva no {assinatura.cartao.nome}.")
        return _aba(conta, "contas")
    return render(request, "financas/assinatura_form.html", {
        "form": form, "conta": conta, "cartoes": cartoes,
        "cartoes_dados": [{"id": c.pk, "nome": c.nome, "fechamento": c.dia_fechamento,
                           "vencimento": c.dia_vencimento} for c in cartoes],
        "item": assinatura if assinatura.pk else None,
    })


@require_POST
@com_familia
def assinatura_excluir(request, assinatura_pk):
    assinatura = get_object_or_404(AssinaturaCartao, pk=assinatura_pk, cartao__conta__familia=request.familia)
    conta = assinatura.cartao.conta
    messages.success(request, f"Assinatura “{assinatura.descricao}” excluída. Para manter o histórico, "
                              "da próxima vez prefira informar a data de cancelamento.")
    assinatura.delete()
    return _aba(conta, "contas")


def _por_mes(inicio, fim, funcao):
    resultado = {}
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        d = date(ano, mes, 1)
        resultado[f"{ano}-{mes:02d}"] = funcao(d)
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return resultado


MESES_PROJECAO = 24


def _projecao_sem_compra(conta, compra):
    """Saldo esperado dia a dia nos próximos meses, sem a compra (em centavos).

    Na edição, as parcelas atuais da própria compra são devolvidas ao saldo, para o
    navegador aplicar as parcelas do formulário por cima.
    """
    inicio = max(date.today(), conta.data_inicio)
    ano_fim, mes_fim = divmod(inicio.year * 12 + inicio.month - 1 + MESES_PROJECAO, 12)
    fim = date(ano_fim, mes_fim + 1, 1) - timedelta(days=1)
    valores = serie_diaria(conta, inicio, fim)

    if compra.pk:
        salva = CompraCartao.objects.select_related("cartao").get(pk=compra.pk)
        for p in cronograma(salva):
            if p.vencimento < conta.data_inicio:
                continue
            desde = max((p.vencimento - inicio).days, 0)
            for i in range(desde, len(valores)):
                valores[i] += p.valor

    # total de cada fatura já prevista (sem esta compra), por cartão e data de fechamento
    totais_faturas = {}
    for f in faturas(conta, inicio, fim, sem_compra=compra.pk):
        totais_faturas.setdefault(str(f.cartao.pk), {})[f.fechamento.isoformat()] = int(f.total * 100)

    return {
        "inicio": inicio.isoformat(),
        "centavos": [int(v * 100) for v in valores],
        "salario": float(conta.salario),
        "reserva": float(conta.reserva),
        # reserva e salário de cada mês da projeção ("AAAA-MM": centavos)
        "reservas": _por_mes(inicio, fim, lambda d: int(conta.reserva_em(d) * 100)),
        "salarios": _por_mes(inicio, fim, lambda d: int(conta.salario_em(d) * 100)),
        "meses": MESES_PROJECAO,
        "faturas": totais_faturas,
    }


@require_POST
@com_familia
def compra_excluir(request, compra_pk):
    compra = get_object_or_404(CompraCartao, pk=compra_pk, cartao__conta__familia=request.familia)
    conta = compra.cartao.conta
    messages.success(request, f"Compra “{compra.descricao}” e todas as parcelas foram excluídas.")
    compra.delete()
    return _aba(conta, "contas")


@com_familia
def item_novo(request, pk, modelo):
    conta = get_object_or_404(Conta, pk=pk, familia=request.familia)
    Model, Form, titulo = _modelo(modelo)
    form = Form(request.POST or None, initial={"data": _data_param(request, "data") or date.today()})
    if form.is_valid():
        item = form.save(commit=False)
        item.conta = conta
        item.save()
        return _voltar_ao_painel(modelo, item)
    return render(request, TEMPLATES_ITEM.get(modelo, "financas/form.html"),
                  {"form": form, "titulo": titulo, "conta": conta,
                   **_contexto_item(modelo, conta, form)})


@com_familia
def item_editar(request, modelo, item_pk):
    Model, Form, titulo = _modelo(modelo)
    item = get_object_or_404(Model, pk=item_pk, conta__familia=request.familia)
    form = Form(request.POST or None, instance=item)
    if form.is_valid():
        form.save()
        return _voltar_ao_painel(modelo, item, request.GET.get("voltar"))
    return render(request, TEMPLATES_ITEM.get(modelo, "financas/form.html"),
                  {"form": form, "titulo": f"Editar: {titulo}", "conta": item.conta, "item": item,
                   "excluir": (modelo, item.pk), "voltar": request.GET.get("voltar", ""),
                   "url_voltar": _destino_painel(modelo, item, request.GET.get("voltar")),
                   **_contexto_item(modelo, item.conta, form, item)})


@require_POST
@com_familia
def item_excluir(request, modelo, item_pk):
    Model, _, _ = _modelo(modelo)
    item = get_object_or_404(Model, pk=item_pk, conta__familia=request.familia)
    destino = _voltar_ao_painel(modelo, item, request.POST.get("voltar"))
    if modelo == "lancamento":
        messages.success(request, f"Lançamento “{item.descricao}” de {item.data:%d/%m} excluído.")
    elif modelo == "fixa":
        messages.success(request, f"Conta fixa “{item.nome}” excluída de todos os meses.")
    item.delete()
    return destino


@require_POST
@com_familia
def recomecar(request, item_pk):
    """Usa o saldo real conferido como novo ponto de partida (zera a diferença)."""
    conferencia = get_object_or_404(Conferencia, pk=item_pk, conta__familia=request.familia)
    conta = conferencia.conta
    conta.saldo_inicial = conferencia.saldo_real
    conta.data_inicio = conferencia.data + timedelta(days=1)
    conta.save()
    return redirect(reverse("financas:painel", args=[conta.pk]) + "#conferencias")
