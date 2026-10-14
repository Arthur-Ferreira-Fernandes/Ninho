"""Carteira de investimentos: quanto deveria haver investido, com rendimento, e se o real bate.

Esperado numa data = saldo informado (investido_inicial em investido_em) + tudo o que foi guardado
depois (guardado do mês, confirmado ou previsto, e avulsos), cada valor rendendo juros compostos
desde o dia em que entrou: valor × (1 + taxa ao ano) ^ (dias / 365).

É uma estimativa bruta: não desconta imposto de renda nem considera taxas que mudam ao longo do
tempo (CDI) ou resgates. Por isso a comparação com o valor real tem uma folga.
"""

import calendar
from datetime import timedelta
from decimal import Decimal

from .calculos import guardados

CENTAVO = Decimal("0.01")
FOLGA = Decimal("0.01")          # até 1% de diferença conta como "dentro do esperado"
FOLGA_MINIMA = Decimal("5")


def configurada(conta):
    return bool(conta.investido_em and conta.investido_inicial is not None) or bool(conta.rendimento_anual)


def _fator(taxa_anual, dias):
    if not taxa_anual or dias <= 0:
        return 1.0
    return (1 + float(taxa_anual) / 100) ** (dias / 365)


def entradas(conta, ate):
    """O que entrou na carteira até `ate`: [(data, valor, descrição)], em ordem de data.

    O que foi guardado até o dia do saldo informado já está dentro dele e não conta de novo.
    """
    inicio = conta.investido_em
    itens = []
    if inicio and conta.investido_inicial:
        itens.append((inicio, conta.investido_inicial, "Saldo informado"))
    for g in guardados(conta, conta.data_inicio, ate):
        if g.valor > 0 and (inicio is None or g.data > inicio):
            itens.append((g.data, g.valor, g.nome))
    # o que foi tirado sai da carteira (e deixa de render) a partir do dia da retirada
    for r in conta.resgates.filter(data__lte=ate):
        if inicio is None or r.data > inicio:
            itens.append((r.data, -r.valor, r.nome))
    itens.sort(key=lambda i: i[0])
    return itens


def valor_em(conta, data, itens):
    """Valor esperado da carteira ao fim de `data`, a partir das entradas já calculadas."""
    total = sum(float(v) * _fator(conta.rendimento_anual, (data - d).days) for d, v, _ in itens if d <= data)
    return Decimal(str(total)).quantize(CENTAVO)


def valor_esperado(conta, data):
    return valor_em(conta, data, entradas(conta, data))


def situacao(diferenca, esperado):
    folga = max(FOLGA_MINIMA, abs(esperado) * FOLGA)
    if diferenca < -folga:
        return "abaixo"
    if diferenca > folga:
        return "acima"
    return "dentro"


def carteira(conta, hoje, meses=12):
    """Resumo da carteira para a aba Investimentos (None se não foi configurada)."""
    if not configurada(conta):
        return None
    fim_projecao = hoje + timedelta(days=365)
    itens = entradas(conta, fim_projecao)
    ate_hoje = [i for i in itens if i[0] <= hoje]
    esperado_hoje = valor_em(conta, hoje, itens)
    aplicado = sum((v for d, v, _ in ate_hoje), Decimal("0"))

    conferencias = []
    for c in conta.conferencias_investimento.all()[:24]:
        esperado = valor_em(conta, c.data, itens)
        dif = c.valor - esperado
        conferencias.append({"obj": c, "esperado": esperado, "diferenca": dif, "situacao": situacao(dif, esperado)})

    return {
        "esperado_hoje": esperado_hoje,
        "aplicado": aplicado,
        "rendimento_ate_hoje": esperado_hoje - aplicado,
        "rendimento_mes": _rendimento_por_mes(conta, hoje, esperado_hoje),
        "em_12_meses": valor_em(conta, fim_projecao, itens),
        "ultima": conferencias[0] if conferencias else None,
        "conferencias": conferencias,
        "meses": mes_a_mes(conta, hoje, itens, conferencias, meses),
    }


def _rendimento_por_mes(conta, hoje, saldo):
    """Quanto o saldo de hoje rende num mês, na taxa informada."""
    if not conta.rendimento_anual:
        return Decimal("0")
    return (saldo * Decimal(str(_fator(conta.rendimento_anual, 365 / 12) - 1))).quantize(CENTAVO)


def mes_a_mes(conta, hoje, itens, conferencias, meses):
    """Do mês mais recente para trás: entrou, rendeu, esperado no fim e o real conferido no mês."""
    primeiro = (conta.investido_em or conta.data_inicio).replace(day=1)
    mes = hoje.replace(day=1)
    linhas = []
    while mes >= primeiro and len(linhas) < meses:
        ultimo = mes.replace(day=calendar.monthrange(mes.year, mes.month)[1])
        fim = min(ultimo, hoje)
        antes = mes - timedelta(days=1)
        inicio = valor_em(conta, antes, itens)
        esperado = valor_em(conta, fim, itens)
        entrou = sum((v for d, v, _ in itens if mes <= d <= fim and v > 0), Decimal("0"))
        saiu = -sum((v for d, v, _ in itens if mes <= d <= fim and v < 0), Decimal("0"))
        conf = next((c for c in conferencias if mes <= c["obj"].data <= ultimo), None)
        linhas.append({"mes": mes, "fim": fim, "parcial": fim < ultimo, "entrou": entrou, "saiu": saiu,
                       "rendeu": esperado - inicio - entrou + saiu, "esperado": esperado, "conferencia": conf})
        mes = (mes - timedelta(days=1)).replace(day=1)
    return linhas
