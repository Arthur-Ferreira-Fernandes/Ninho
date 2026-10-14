"""Análise: para onde vai o dinheiro, quanto se guarda e o que merece atenção.

Tudo parte do extrato do mês (extrato_mes), então os totais por categoria batem com o
"gasto" da página do mês e do extrato mensal.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .calculos import (ZERO, _somar_meses, cronograma, dia_de_guardar, extrato_mes, guardados, saldo_esperado,
                       simular_mes, totais_do_mes)
from .models import Aporte, AssinaturaCartao, CompraCartao, ContaFixa, PagamentoConta, UsoDiario

DIA_A_DIA = "dia"      # dinheiro do dia a dia sem gastos detalhados (não dá para saber a categoria)


def primeiro_dia(d):
    return d.replace(day=1)


def ultimo_dia(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def somar_meses(d, n):
    ano, mes = _somar_meses(d.year, d.month, n)
    return date(ano, mes, 1)


def meses_entre(inicio, fim):
    """Quantos meses de inicio até fim (ambos 1º dia do mês)."""
    return (fim.year - inicio.year) * 12 + fim.month - inicio.month


def extrato(conta, mes):
    """extrato_mes com cache por instância da conta (a análise usa o mesmo mês várias vezes)."""
    cache = conta.__dict__.setdefault("_cache_extrato", {})
    chave = (mes.year, mes.month)
    if chave not in cache:
        cache[chave] = extrato_mes(conta, mes.year, mes.month)
    return cache[chave]


def mes_completo(conta, mes):
    """O cálculo cobre o mês inteiro (não começou no meio dele)."""
    return mes >= conta.data_inicio


def meses_fechados(conta, hoje, quantidade):
    """Os últimos meses já encerrados e completos, do mais recente para o mais antigo."""
    resultado, mes = [], somar_meses(primeiro_dia(hoje), -1)
    while len(resultado) < quantidade and ultimo_dia(mes) >= conta.data_inicio:
        if mes_completo(conta, mes):
            resultado.append(mes)
        mes = somar_meses(mes, -1)
    return resultado


# ---------------------------------------------------------------------------
# Gastos por categoria
# ---------------------------------------------------------------------------

@dataclass
class ItemGasto:
    data: date
    nome: str
    valor: Decimal
    chave: object          # pk da categoria, None (sem categoria) ou DIA_A_DIA
    previsto: bool = False


def itens_de_gasto(dias, hoje):
    """Tudo o que foi gasto no mês, item por item, com a categoria de cada um.

    Mesma soma do "gasto" de totais_do_mes(): contas fixas, lançamentos de saída, faturas
    (parcela a parcela e assinatura a assinatura) e o dinheiro do dia a dia. O guardado não entra.
    """
    itens = []
    for d in dias:
        if not d.ativo:
            continue
        futuro = d.data > hoje
        for m in d.movimentos:
            if m.entrada or m.origem == "guardar":
                continue
            if m.origem == "fatura":
                for p in m.objeto.parcelas:
                    rotulo = f" ({p.rotulo})" if p.compra.parcelas > 1 else ""
                    itens.append(ItemGasto(d.data, p.compra.descricao + rotulo, p.valor, p.compra.categoria_id, futuro))
                for c in m.objeto.cobrancas:
                    itens.append(ItemGasto(d.data, c.assinatura.descricao, c.valor, c.assinatura.categoria_id, futuro))
            elif m.origem == "fixa":
                feito = m.evento is not None and m.evento.feito
                itens.append(ItemGasto(d.data, m.nome, m.valor, m.objeto.categoria_id, futuro and not feito))
            else:
                itens.append(ItemGasto(d.data, m.nome, m.valor, m.objeto.categoria_id, futuro))
        if d.itens:
            itens += [ItemGasto(d.data, g.descricao or "Gasto do dia", g.valor, g.categoria_id) for g in d.itens]
        elif d.gasto:
            itens.append(ItemGasto(d.data, "Dinheiro do dia a dia", d.gasto, DIA_A_DIA, futuro))
    return itens


def totais_por_chave(itens):
    totais = {}
    for i in itens:
        totais[i.chave] = totais.get(i.chave, ZERO) + i.valor
    return totais


def _rotulo(chave, categorias):
    if chave == DIA_A_DIA:
        return "Dia a dia sem detalhar", "dia"
    if chave is None or chave not in categorias:
        return "Sem categoria", "sem"
    return categorias[chave].nome, categorias[chave].cor


def media_por_chave(conta, meses, hoje):
    """Média mensal de cada categoria nos meses dados ({} se não houver meses)."""
    if not meses:
        return {}
    soma = {}
    for mes in meses:
        for chave, total in totais_por_chave(itens_de_gasto(extrato(conta, mes), hoje)).items():
            soma[chave] = soma.get(chave, ZERO) + total
    return {chave: (total / len(meses)).quantize(Decimal("0.01")) for chave, total in soma.items()}


def categorias_do_mes(conta, mes, hoje, categorias, medias=None):
    """Gasto de cada categoria no mês, do maior para o menor, com limite e média dos meses anteriores.

    No mês em andamento a variação em % não é calculada: comparar meio mês com meses inteiros engana.
    O dia a dia sem detalhar não tem média: nos meses passados ele depende de quanto foi detalhado.
    """
    itens = itens_de_gasto(extrato(conta, mes), hoje)
    totais = totais_por_chave(itens)
    medias = {k: v for k, v in (medias or {}).items() if k != DIA_A_DIA}
    parcial = ultimo_dia(mes) >= hoje
    total_mes = sum(totais.values(), ZERO)
    linhas = []
    for chave in set(totais) | {c for c in medias if medias[c] > 0}:
        nome, cor = _rotulo(chave, categorias)
        total = totais.get(chave, ZERO)
        categoria = categorias.get(chave) if chave not in (None, DIA_A_DIA) else None
        limite = categoria.limite_mensal if categoria else None
        media = medias.get(chave)
        linhas.append({
            "chave": chave, "nome": nome, "cor": cor, "categoria": categoria, "total": total,
            "pct": round(total * 100 / total_mes) if total_mes else 0,
            "itens": sorted((i for i in itens if i.chave == chave), key=lambda i: (-i.valor, i.data)),
            "previsto": sum((i.valor for i in itens if i.chave == chave and i.previsto), ZERO),
            "limite": limite,
            "pct_limite": round(total * 100 / limite) if limite else None,
            "media": media,
            "variacao": (total - media) if media is not None else None,
            "variacao_pct": round((total - media) * 100 / media) if media and not parcial else None,
        })
    linhas.sort(key=lambda l: (l["chave"] == DIA_A_DIA, -l["total"]))
    # no gráfico, a cor segue a posição: as maiores categorias nunca repetem cor
    for i, linha in enumerate(x for x in linhas if x["categoria"]):
        linha["cor"] = i % 8
    maior = max((max(l["total"], l["media"] or ZERO, l["limite"] or ZERO) for l in linhas), default=ZERO)
    for l in linhas:
        l["largura"] = float(l["total"] * 100 / maior) if maior else 0
        l["largura_media"] = float(l["media"] * 100 / maior) if maior and l["media"] else None
        l["largura_limite"] = float(l["limite"] * 100 / maior) if maior and l["limite"] else None
    return {"linhas": [l for l in linhas if l["total"] or l["media"]], "total": total_mes, "parcial": parcial}


def variacoes(linhas, minimo=Decimal("30")):
    """Categorias que mais subiram e mais caíram em relação à média (diferença relevante)."""
    def relevante(l):
        if l["chave"] == DIA_A_DIA or l["media"] is None:
            return False
        dif = abs(l["variacao"])
        return dif >= minimo and (not l["media"] or dif >= l["media"] * Decimal("0.15"))
    candidatas = [l for l in linhas if relevante(l)]
    subiram = sorted((l for l in candidatas if l["variacao"] > 0), key=lambda l: -l["variacao"])[:3]
    cairam = sorted((l for l in candidatas if l["variacao"] < 0), key=lambda l: l["variacao"])[:2]
    return subiram, cairam


# ---------------------------------------------------------------------------
# Poupança: taxa por mês e sequência de meses guardando
# ---------------------------------------------------------------------------

def _guardado_do_mes(conta, mes):
    """(valor guardado no mês, situação): mensal pela referência + avulsos feitos no mês."""
    mensal = dia_de_guardar(conta, mes.year, mes.month)
    avulsos = sum((g.valor for g in guardados(conta, mes, ultimo_dia(mes)) if g.avulso), ZERO)
    if mensal is None:
        situacao = "sem_meta"
        valor_mensal = ZERO
    elif mensal.nao_guardado:
        situacao, valor_mensal = "nao_guardado", ZERO
    elif mensal.confirmado:
        situacao, valor_mensal = "confirmado", mensal.valor
    else:
        situacao, valor_mensal = "previsto", mensal.valor
    return valor_mensal + avulsos, situacao, avulsos


def historico_poupanca(conta, hoje, meses=12):
    """Por mês (do mais antigo ao atual): recebido, guardado e a taxa de poupança."""
    atual = primeiro_dia(hoje)
    inicio = max(somar_meses(atual, -(meses - 1)), primeiro_dia(conta.data_inicio))
    linhas, mes = [], inicio
    while mes <= atual:
        t = totais_do_mes(extrato(conta, mes))
        guardado, situacao, avulsos = _guardado_do_mes(conta, mes)
        recebido = t["recebido"] if t else ZERO
        linhas.append({
            "mes": mes, "atual": mes == atual, "completo": mes_completo(conta, mes),
            "recebido": recebido, "guardado": guardado, "avulsos": avulsos, "situacao": situacao,
            "taxa": round(guardado * 100 / recebido, 1) if recebido else None,
        })
        mes = somar_meses(mes, 1)
    maior = max((l["taxa"] or 0 for l in linhas), default=0)
    for l in linhas:
        l["altura"] = float((l["taxa"] or 0) * 100 / maior) if maior else 0
    fechados = [l for l in linhas if not l["atual"] and l["completo"] and l["taxa"] is not None
                and l["situacao"] != "previsto"]
    ultimos = fechados[-6:]
    return {
        "linhas": linhas,
        "media": round(sum(l["taxa"] for l in ultimos) / len(ultimos), 1) if ultimos else None,
        "meses_media": len(ultimos),
        "sequencia": sequencia_guardando(linhas),
    }


def meses_sem_confirmar(conta, hoje, meses=6):
    """Meses já encerrados (dos últimos `meses`) com guardado previsto e ainda sem confirmação."""
    atual = primeiro_dia(hoje)
    resultado = []
    for i in range(1, meses + 1):
        mes = somar_meses(atual, -i)
        if ultimo_dia(mes) < conta.data_inicio:
            break
        g = dia_de_guardar(conta, mes.year, mes.month)
        if g and not g.confirmado:
            resultado.append(mes)
    return resultado


def sequencia_guardando(linhas):
    """Meses seguidos guardando algo, contando do mais recente para trás.

    O mês atual só conta se já guardou (ainda dá tempo). Um mês passado sem confirmação
    interrompe a contagem: só conta o que foi confirmado.
    """
    seguidos = 0
    for l in reversed(linhas):
        guardou = l["avulsos"] > 0 or (l["situacao"] == "confirmado" and l["guardado"] > 0)
        if l["atual"] and not guardou:
            continue
        if not guardou:
            break
        seguidos += 1
    return seguidos


# ---------------------------------------------------------------------------
# Fechamento do mês
# ---------------------------------------------------------------------------

def fechamento(conta, mes, hoje, categorias):
    """Resumo de um mês: quanto entrou, saiu e sobrou, o que mudou e o que fazer."""
    dias = extrato(conta, mes)
    totais = totais_do_mes(dias)
    if not totais:
        return None
    anteriores = [m for m in meses_fechados(conta, hoje, 12) if m < mes][:3]
    medias = media_por_chave(conta, anteriores, hoje)
    cats = categorias_do_mes(conta, mes, hoje, categorias, medias)
    subiram, cairam = variacoes(cats["linhas"]) if anteriores else ([], [])
    encerrado = ultimo_dia(mes) < hoje

    ativos = [d for d in dias if d.ativo]
    orcamento = sum((o.base for o in simular_mes(conta, mes.year, mes.month)), ZERO)
    usado = sum((d.gasto for d in ativos), ZERO)
    guardado, situacao, _ = _guardado_do_mes(conta, mes)
    previsto_guardar = conta.guardar_mensal_em(mes)

    sobra = totais["sobra"]
    ja_guardou_sobra = Aporte.objects.filter(conta=conta, sobra_de=mes).exists()
    sugestoes = []
    if sobra > 0 and encerrado and not ja_guardou_sobra:
        sugestoes.append({"tipo": "sobra", "texto": f"Pelo planejado, sobraram {_brl(sobra)} neste mês. "
                          "Que tal guardar esse valor antes que ele se dilua no mês seguinte?"})
    if sobra < 0:
        causa = f" O que mais subiu foi {subiram[0]['nome']} (+{_brl(subiram[0]['variacao'])})." if subiram else ""
        sugestoes.append({"tipo": "falta", "texto": f"O mês fechou {_brl(-sobra)} no vermelho.{causa}"})
    if encerrado and usado > orcamento * Decimal("1.03"):
        sugestoes.append({"tipo": "dia", "texto": f"O dia a dia passou {_brl(usado - orcamento)} do combinado "
                          f"({_brl(usado)} de {_brl(orcamento)})."})
    for l in cats["linhas"]:
        if l["pct_limite"] is not None and l["pct_limite"] >= 100:
            sugestoes.append({"tipo": "limite", "texto": f"{l['nome']} passou do limite: {_brl(l['total'])} "
                              f"de {_brl(l['limite'])}."})
    if totais["recebido"] > 0:
        taxa = guardado * 100 / totais["recebido"]
        if taxa < 10:
            dez = (totais["recebido"] / 10).quantize(Decimal("1"))
            sugestoes.append({"tipo": "taxa", "texto": f"Você guardou {taxa:.0f}% do que entrou. Guardar 10% "
                              f"seriam {_brl(dez)} por mês."})
    return {
        "mes": mes, "encerrado": encerrado, "totais": totais, "categorias": cats,
        "subiram": subiram, "cairam": cairam, "comparado_com": len(anteriores),
        "dia_a_dia": {"orcamento": orcamento, "usado": usado, "diferenca": orcamento - usado},
        "guardar": {"previsto": previsto_guardar, "guardado": guardado, "situacao": situacao},
        "sobra": sobra, "ja_guardou_sobra": ja_guardou_sobra, "sugestoes": sugestoes,
    }


def _brl(valor):
    from .templatetags.dinheiro import brl
    return brl(valor)


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------

def ritmo_dia_a_dia(conta, hoje):
    """No ritmo dos dias já informados, o dinheiro do dia a dia do mês dura até quando?"""
    dias = simular_mes(conta, hoje.year, hoje.month)
    passados = [d for d in dias if d.data <= hoje]
    informados = [d for d in passados if d.itens or d.uso]
    if not dias or not informados:
        return None
    total = sum((d.base for d in dias), ZERO)
    usado = sum((d.gasto for d in passados), ZERO)
    if total <= 0:
        return None
    por_dia = usado / len(passados)
    projecao = (por_dia * len(dias)).quantize(Decimal("0.01"))
    resultado = {"usado": usado, "total": total, "por_dia": por_dia.quantize(Decimal("0.01")),
                 "planejado_dia": (total / len(dias)).quantize(Decimal("0.01")), "projecao": projecao,
                 "acaba_em": None, "sobra": total - projecao}
    if por_dia > 0 and projecao > total * Decimal("1.05"):
        resultado["acaba_em"] = dias[0].data + timedelta(days=max(0, math.ceil(total / por_dia) - 1))
    return resultado


def contas_variaveis_acima(conta, limite=Decimal("1.20"), minimo=Decimal("20")):
    """Contas variáveis cujo último valor pago veio bem acima da média dos anteriores."""
    resultado = []
    for fixa in conta.fixas.filter(variavel=True, ativa=True, tipo=ContaFixa.DESPESA):
        pagos = list(fixa.pagamentos.filter(situacao=PagamentoConta.PAGO).order_by("-referencia")[:7])
        if len(pagos) < 3:
            continue
        ultimo, anteriores = pagos[0], pagos[1:]
        media = sum((p.valor for p in anteriores), ZERO) / len(anteriores)
        if ultimo.valor > media * limite and ultimo.valor - media >= minimo:
            resultado.append({"fixa": fixa, "valor": ultimo.valor, "media": media.quantize(Decimal("0.01")),
                              "pct": round((ultimo.valor - media) * 100 / media), "referencia": ultimo.referencia,
                              "meses": len(anteriores)})
    return resultado


def vazamento(conta, hoje):
    """Diferença entre o saldo real (última conferência) e o esperado, por mês desde o início."""
    c = conta.conferencias.filter(data__gte=conta.data_inicio, data__lte=hoje).order_by("-data", "-id").first()
    if not c:
        return None
    diferenca = c.saldo_real - saldo_esperado(conta, c.data).esperado
    meses = max(Decimal("1"), Decimal((c.data - conta.data_inicio).days + 1) / Decimal("30.4"))
    por_mes = (diferenca / meses).quantize(Decimal("0.01"))
    referencia = max(Decimal("50"), conta.salario * Decimal("0.02"))
    return {"conferencia": c, "diferenca": diferenca, "por_mes": por_mes,
            "relevante": diferenca < 0 and -por_mes >= referencia,
            "dias_sem_conferir": (hoje - c.data).days}


def compromisso_cartao(conta, hoje, meses=6):
    """Parcelas de compras (sem assinaturas) que vencem em cada um dos próximos meses e % do salário."""
    atual = primeiro_dia(hoje)
    por_mes = {somar_meses(atual, i): ZERO for i in range(1, meses + 1)}
    restante = ZERO
    for compra in CompraCartao.objects.filter(cartao__conta=conta).select_related("cartao"):
        for p in cronograma(compra):
            if p.vencimento > hoje:
                restante += p.valor
                mes = primeiro_dia(p.vencimento)
                if mes in por_mes:
                    por_mes[mes] += p.valor
    linhas = []
    for mes, valor in por_mes.items():
        salario = conta.salario_em(mes)
        linhas.append({"mes": mes, "valor": valor, "salario": salario,
                       "pct": round(valor * 100 / salario) if salario else None})
    maior = max((l["valor"] for l in linhas), default=ZERO)
    for l in linhas:
        l["largura"] = float(l["valor"] * 100 / maior) if maior else 0
    return {"linhas": linhas, "restante": restante, "proximo": linhas[0] if linhas else None}


def assinaturas_anuais(conta, hoje, revisar_apos=180):
    """Assinaturas ativas com o custo de um ano e se já é hora de revisar se ainda usa."""
    lista = []
    for a in AssinaturaCartao.objects.filter(cartao__conta=conta).select_related("cartao", "categoria"):
        if not (a.ativa_em(hoje) or a.inicio > hoje):
            continue
        desde = a.revisada_em or a.inicio
        lista.append({"assinatura": a, "anual": a.valor * 12, "revisar": (hoje - desde).days >= revisar_apos,
                      "desde": desde})
    lista.sort(key=lambda x: -x["anual"])
    return {"lista": lista, "mensal": sum((x["assinatura"].valor for x in lista), ZERO),
            "anual": sum((x["anual"] for x in lista), ZERO),
            "a_revisar": [x for x in lista if x["revisar"]]}


def liberacoes(conta, hoje, antes=2, depois=6):
    """Parcelas, contas e assinaturas que acabam: a partir de quando e quanto sobra por mês.

    Mostra o que libera entre `antes` meses atrás e `depois` meses à frente, menos o que já foi visto.
    """
    atual = primeiro_dia(hoje)
    de, ate = somar_meses(atual, -antes), somar_meses(atual, depois)
    visto = conta.liberado_visto_ate
    itens = []

    def incluir(mes, nome, valor, origem):
        if de <= mes <= ate and (visto is None or mes > visto) and valor > 0:
            itens.append({"mes": mes, "nome": nome, "valor": valor, "origem": origem})

    for compra in CompraCartao.objects.filter(cartao__conta=conta, parcelas__gt=1).select_related("cartao"):
        parcelas = cronograma(compra)
        incluir(somar_meses(primeiro_dia(parcelas[-1].vencimento), 1), compra.descricao, parcelas[-1].valor, "parcelas")
    for fixa in conta.fixas.filter(ativa=True, tipo=ContaFixa.DESPESA, vigente_ate__isnull=False):
        incluir(somar_meses(primeiro_dia(fixa.vigente_ate), 1), fixa.nome, fixa.valor_mensal, "conta")
    for a in AssinaturaCartao.objects.filter(cartao__conta=conta, fim__isnull=False):
        mes = primeiro_dia(a.fim) if a.fim.day <= a.dia_cobranca else somar_meses(primeiro_dia(a.fim), 1)
        incluir(mes, a.descricao, a.valor, "assinatura")

    itens.sort(key=lambda i: (i["mes"], -i["valor"]))
    ja = [i for i in itens if i["mes"] <= atual]
    return {"ja": ja, "em_breve": [i for i in itens if i["mes"] > atual],
            "total_ja": sum((i["valor"] for i in ja), ZERO)}


def projecao_objetivo(objetivo, conta, hoje):
    """Quanto falta, em que ritmo se está guardando para ele e quando chega."""
    falta = objetivo.falta
    ritmo = None
    if objetivo.padrao and conta.guardar_mensal > 0:
        ritmo = conta.guardar_mensal
    else:
        recentes = [a.valor for a in objetivo.aportes.all() if a.data > hoje - timedelta(days=183)]
        if recentes:
            ritmo = (sum(recentes, ZERO) / 6).quantize(Decimal("0.01"))
    chega_em = None
    if falta > 0 and ritmo:
        chega_em = somar_meses(primeiro_dia(hoje), math.ceil(falta / ritmo))
    por_mes_prazo = None
    if objetivo.prazo and falta > 0:
        meses = max(1, meses_entre(primeiro_dia(hoje), primeiro_dia(objetivo.prazo)))
        por_mes_prazo = (falta / meses).quantize(Decimal("0.01"))
    return {"objetivo": objetivo, "falta": falta, "ritmo": ritmo, "chega_em": chega_em,
            "por_mes_prazo": por_mes_prazo,
            "no_prazo": None if not (objetivo.prazo and chega_em) else chega_em <= primeiro_dia(objetivo.prazo)}


def alertas(conta, hoje, categorias, cats_atual=None):
    """O que merece atenção agora, do mais grave para o mais leve.

    nivel: "perigo", "atencao", "info" ou "bom". ancora: seção da página de análise.
    """
    lista = []

    r = ritmo_dia_a_dia(conta, hoje)
    if r and r["acaba_em"]:
        lista.append({"nivel": "perigo" if r["acaba_em"].day <= 20 else "atencao", "ancora": "ritmo",
                      "titulo": f"No ritmo atual, o dinheiro do dia a dia acaba por volta do dia {r['acaba_em'].day}",
                      "texto": f"Você está usando {_brl(r['por_dia'])} por dia; o planejado é {_brl(r['planejado_dia'])}."})
    elif r and r["sobra"] > r["total"] * Decimal("0.1"):
        lista.append({"nivel": "bom", "ancora": "ritmo",
                      "titulo": f"No ritmo atual, devem sobrar {_brl(r['sobra'])} do dia a dia",
                      "texto": f"Você está usando {_brl(r['por_dia'])} por dia, abaixo dos {_brl(r['planejado_dia'])} planejados."})

    for linha in (cats_atual or {}).get("linhas", []):
        p = linha["pct_limite"]
        if p is not None and p >= 80:
            lista.append({"nivel": "perigo" if p >= 100 else "atencao", "ancora": "categorias",
                          "titulo": f"{linha['nome']}: {p}% do limite do mês",
                          "texto": f"{_brl(linha['total'])} de {_brl(linha['limite'])}."})

    for v in contas_variaveis_acima(conta):
        lista.append({"nivel": "atencao", "ancora": "alertas",
                      "titulo": f"{v['fixa'].nome} veio {v['pct']}% acima da média",
                      "texto": f"{_brl(v['valor'])} em {v['referencia']:%m/%Y}; a média dos {v['meses']} meses "
                               f"anteriores é {_brl(v['media'])}."})

    vaz = vazamento(conta, hoje)
    if vaz and vaz["relevante"]:
        lista.append({"nivel": "atencao", "ancora": "alertas",
                      "titulo": f"O saldo real está {_brl(-vaz['diferenca'])} abaixo do esperado",
                      "texto": f"Cerca de {_brl(-vaz['por_mes'])} por mês desde o início do cálculo. Pode haver "
                               "gastos que não estão sendo lançados (pix, débito, saques)."})
    if vaz and vaz["dias_sem_conferir"] >= 30:
        lista.append({"nivel": "info", "ancora": "alertas",
                      "titulo": f"Faz {vaz['dias_sem_conferir']} dias que o saldo não é conferido",
                      "texto": "Conferir o saldo do banco mostra cedo se algo está fugindo do planejado."})

    cartao = compromisso_cartao(conta, hoje)
    prox = cartao["proximo"]
    if prox and prox["pct"] is not None and prox["pct"] >= 30:
        lista.append({"nivel": "perigo" if prox["pct"] >= 50 else "atencao", "ancora": "cartao",
                      "titulo": f"{prox['pct']}% do salário de {prox['mes']:%m/%Y} já está em parcelas",
                      "texto": f"{_brl(prox['valor'])} em parcelas no cartão; {_brl(cartao['restante'])} a pagar no total."})

    pendentes = meses_sem_confirmar(conta, hoje)
    if pendentes:
        lista.append({"nivel": "info", "ancora": "poupanca",
                      "titulo": f"{len(pendentes)} {'mês' if len(pendentes) == 1 else 'meses'} sem confirmar o guardado",
                      "texto": "Confirme em Investimentos se guardou ou não: a sequência e a taxa de poupança só contam o confirmado."})

    revisar = assinaturas_anuais(conta, hoje)["a_revisar"]
    if revisar:
        anual = sum((x["anual"] for x in revisar), ZERO)
        lista.append({"nivel": "info", "ancora": "assinaturas",
                      "titulo": f"{len(revisar)} assinatura{'s' if len(revisar) > 1 else ''} para revisar",
                      "texto": f"Somam {_brl(anual)} por ano. Ainda usa todas?"})

    lib = liberacoes(conta, hoje)
    if lib["ja"]:
        lista.append({"nivel": "bom", "ancora": "liberado",
                      "titulo": f"{_brl(lib['total_ja'])} por mês ficaram livres",
                      "texto": "Parcelas ou contas que acabaram. Um bom momento para aumentar o guardado."})

    ordem = {"perigo": 0, "atencao": 1, "bom": 2, "info": 3}
    lista.sort(key=lambda a: ordem[a["nivel"]])
    return lista


def uso_registrado(conta):
    return conta.gastos_dia.exists() or conta.usos.exclude(situacao=UsoDiario.TUDO).exists()


def sem_categoria(conta, hoje, dias=90, limite=40):
    """O que saiu nos últimos dias sem categoria, agrupado pela descrição, para classificar.

    [{"tipo", "item" (o mais recente do grupo), "descricao", "quantidade", "total", "data"}]
    """
    desde = hoje - timedelta(days=dias)
    itens = [("gasto", g, g.descricao or "Gasto do dia", g.valor, g.data)
             for g in conta.gastos_dia.filter(categoria__isnull=True, data__gte=desde)]
    itens += [("lancamento", l, l.descricao, l.valor, l.data)
              for l in conta.lancamentos.filter(categoria__isnull=True, tipo="saida", data__gte=desde)]
    itens += [("compra", c, c.descricao, c.valor_total, c.data_compra)
              for c in CompraCartao.objects.filter(cartao__conta=conta, categoria__isnull=True, data_compra__gte=desde)]
    itens += [("assinatura", a, a.descricao, a.valor, a.inicio)
              for a in AssinaturaCartao.objects.filter(cartao__conta=conta, categoria__isnull=True)
              if a.ativa_em(hoje) or a.inicio > hoje]
    itens += [("fixa", f, f.nome, f.valor, None)
              for f in conta.fixas.filter(categoria__isnull=True, ativa=True, tipo=ContaFixa.DESPESA)]
    itens.sort(key=lambda i: (i[4] is not None, i[4] or date.min), reverse=True)
    grupos = {}
    from .categorias import normalizar
    for tipo, item, descricao, valor, data in itens:
        g = grupos.setdefault(normalizar(descricao), {"tipo": tipo, "item": item, "descricao": descricao,
                                                       "quantidade": 0, "total": ZERO, "data": data})
        g["quantidade"] += 1
        g["total"] += valor
    return sorted(grupos.values(), key=lambda g: -g["total"])[:limite]
