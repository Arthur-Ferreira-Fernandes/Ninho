"""Cálculo do saldo esperado.

Saldo esperado ao FINAL do dia `data`:
    saldo_inicial
  + receitas fixas que caíram entre data_inicio e data
  - despesas fixas que venceram entre data_inicio e data
  - dinheiro do dia a dia usado nos dias decorridos (contando data_inicio e data)
  + entradas avulsas - saídas avulsas no período
  - faturas de cartão que vencem no período (parcelas de compras + assinaturas)
  - valor guardado de cada mês, no dia de maior saldo daquele mês

Dinheiro do dia a dia (envelope mensal): cada mês tem o orçamento mensal (proporcional
no mês de início). O valor disponível em um dia é o que resta do orçamento do mês
dividido pelos dias que faltam, então o que não é usado num dia é diluído nos
seguintes. Dias sem registro de uso contam como "usou tudo".
"""

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .models import AssinaturaCartao, CompraCartao, ContaFixa, Lancamento, UsoDiario


def _ocorrencias(fixa, inicio, fim):
    """Datas em que a conta fixa cai entre inicio e fim (inclusive)."""
    datas = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        dia = min(fixa.dia, calendar.monthrange(ano, mes)[1])
        d = date(ano, mes, dia)
        if inicio <= d <= fim:
            datas.append(d)
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
    return datas


@dataclass
class EventoFixa:
    """Uma ocorrência de conta fixa: quando e quanto mexe na conta.

    Contas variáveis usam a estimativa (fixa.valor) até serem marcadas como pagas;
    pagas, usam o valor e a data do pagamento.
    """
    fixa: object
    data: date            # quando o dinheiro sai/entra
    valor: Decimal
    vencimento: date      # data prevista da ocorrência
    pagamento: object = None

    @property
    def estimado(self):
        return self.fixa.variavel and self.pagamento is None

    # mesmos nomes de uma conta fixa, para listas de vencimentos
    @property
    def nome(self):
        return self.fixa.nome

    @property
    def tipo(self):
        return self.fixa.tipo


def _fixas_ativas(conta):
    return conta.fixas.filter(ativa=True).prefetch_related("pagamentos")


def eventos_fixa(fixa, inicio, fim):
    """Ocorrências da conta fixa que mexem na conta entre inicio e fim (inclusive)."""
    if not fixa.variavel:
        return [EventoFixa(fixa, d, fixa.valor, d) for d in _ocorrencias(fixa, inicio, fim)]
    # o pagamento pode sair antes ou depois do vencimento: olha uma folga de 2 meses
    pagos = {(p.referencia.year, p.referencia.month): p for p in fixa.pagamentos.all()}
    eventos = []
    for d in _ocorrencias(fixa, inicio - timedelta(days=62), fim + timedelta(days=62)):
        p = pagos.get((d.year, d.month))
        data, valor = (p.data, p.valor) if p else (d, fixa.valor)
        if inicio <= data <= fim:
            eventos.append(EventoFixa(fixa, data, valor, d, p))
    return eventos


CENTAVO = Decimal("0.01")
ZERO = Decimal("0")


@dataclass
class DiaOrcamento:
    data: date
    base: Decimal         # orçamento do mês ÷ dias do mês, sem sobras
    disponivel: Decimal   # o que pode ser usado no dia, já com sobras/faltas anteriores
    gasto: Decimal        # o que conta como usado
    uso: object = None    # UsoDiario, se a pessoa informou


def _usos(conta, inicio, fim):
    return {u.data: u for u in conta.usos.filter(data__gte=inicio, data__lte=fim)}


def simular_mes(conta, ano, mes, usos=None):
    """Envelope do dia a dia de um mês: um DiaOrcamento por dia ativo."""
    dias_no_mes = calendar.monthrange(ano, mes)[1]
    ultimo = date(ano, mes, dias_no_mes)
    inicio = max(date(ano, mes, 1), conta.data_inicio)
    if inicio > ultimo:
        return []
    if usos is None:
        usos = _usos(conta, inicio, ultimo)

    dias_ativos = (ultimo - inicio).days + 1
    restante = (conta.orcamento_mensal * dias_ativos / dias_no_mes).quantize(CENTAVO)
    base = (conta.orcamento_mensal / dias_no_mes).quantize(CENTAVO)
    resultado = []
    for i in range(dias_ativos):
        d = inicio + timedelta(days=i)
        disponivel = max(restante / (dias_ativos - i), ZERO).quantize(CENTAVO)
        uso = usos.get(d)
        if uso is None or uso.situacao == UsoDiario.TUDO:
            gasto = disponivel
        elif uso.situacao == UsoDiario.NADA:
            gasto = ZERO
        else:
            gasto = uso.valor or ZERO
        restante -= gasto
        resultado.append(DiaOrcamento(d, base, disponivel, gasto, uso))
    return resultado


def gasto_no_periodo(conta, inicio, fim):
    """Dinheiro do dia a dia usado entre inicio e fim (inclusive)."""
    ultimo = date(fim.year, fim.month, calendar.monthrange(fim.year, fim.month)[1])
    usos = _usos(conta, inicio, ultimo)
    total = ZERO
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        for d in simular_mes(conta, ano, mes, usos):
            if inicio <= d.data <= fim:
                total += d.gasto
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return total


@dataclass
class Resumo:
    data: date
    dias: int
    saldo_inicial: Decimal
    gasto_diario_total: Decimal
    receitas_fixas: Decimal
    despesas_fixas: Decimal
    entradas: Decimal
    saidas: Decimal
    esperado: Decimal
    fixas_no_periodo: list = field(default_factory=list)
    faturas: Decimal = Decimal("0")
    guardado: Decimal = Decimal("0")


def saldo_esperado(conta, data):
    if data < conta.data_inicio:
        zero = Decimal("0")
        return Resumo(data, 0, conta.saldo_inicial, zero, zero, zero, zero, zero,
                      conta.saldo_inicial)

    dias = (data - conta.data_inicio).days + 1
    gasto_diario_total = gasto_no_periodo(conta, conta.data_inicio, data)

    receitas = despesas = Decimal("0")
    fixas_no_periodo = []
    for fixa in _fixas_ativas(conta):
        for e in eventos_fixa(fixa, conta.data_inicio, data):
            fixas_no_periodo.append((e.data, fixa))
            if fixa.tipo == ContaFixa.RECEITA:
                receitas += e.valor
            else:
                despesas += e.valor
    fixas_no_periodo.sort(key=lambda x: x[0])

    lancs = conta.lancamentos.filter(data__gte=conta.data_inicio, data__lte=data)
    entradas = sum((l.valor for l in lancs if l.tipo == Lancamento.ENTRADA), Decimal("0"))
    saidas = sum((l.valor for l in lancs if l.tipo == Lancamento.SAIDA), Decimal("0"))

    total_faturas = sum((f.total for f in faturas(conta, conta.data_inicio, data)), ZERO)
    total_guardado = sum((g.valor for g in guardados(conta, conta.data_inicio, data)), ZERO)

    esperado = (conta.saldo_inicial + receitas - despesas
                - gasto_diario_total + entradas - saidas - total_faturas - total_guardado)
    return Resumo(data, dias, conta.saldo_inicial, gasto_diario_total, receitas,
                  despesas, entradas, saidas, esperado, fixas_no_periodo, faturas=total_faturas,
                  guardado=total_guardado)


def proximas_fixas(conta, hoje, quantidade=5):
    """Próximas contas fixas a partir de amanhã."""
    inicio = hoje + timedelta(days=1)
    fim = date(hoje.year + 1, hoje.month, 1)
    itens = []
    for fixa in _fixas_ativas(conta):
        for e in eventos_fixa(fixa, inicio, fim):
            if e.pagamento is None:   # o que já foi pago não é mais um vencimento
                itens.append((e.data, e))
    itens.sort(key=lambda x: x[0])
    return itens[:quantidade]


@dataclass
class Movimento:
    nome: str
    valor: Decimal
    entrada: bool
    origem: str       # "fixa", "lancamento" ou "fatura"
    objeto: object
    evento: object = None   # EventoFixa, para contas fixas (valor estimado ou pago)


@dataclass
class Dia:
    data: date
    ativo: bool                     # dentro do período calculado (>= data_inicio)
    inicio: Decimal = None          # saldo esperado no início do dia
    movimentos: list = field(default_factory=list)
    entradas: Decimal = Decimal("0")
    saidas: Decimal = Decimal("0")
    gasto: Decimal = Decimal("0")   # dinheiro do dia a dia que conta como usado
    disponivel: Decimal = None      # dinheiro do dia a dia disponível
    base: Decimal = None            # valor diário sem sobras
    uso: object = None              # UsoDiario informado
    esperado: Decimal = None        # saldo esperado ao fim do dia
    conferencia: object = None
    diferenca: Decimal = None
    cor: str = ""                   # nível na escala de cores do saldo (ver aplicar_escala)

    @property
    def fim_de_semana(self):
        return self.data.weekday() >= 5

    @property
    def ajuste(self):
        """Quanto o disponível do dia difere do valor diário normal (sobras ou faltas).

        Diferenças de poucos centavos vêm só do arredondamento e são ignoradas.
        """
        if self.disponivel is None:
            return ZERO
        ajuste = self.disponivel - self.base
        return ajuste if abs(ajuste) >= Decimal("0.05") else ZERO


def _dias_com_movimentos(conta, ano, mes, conferencias=True):
    """Dias do mês com fixas, lançamentos e faturas (sem o valor guardado e sem saldos)."""
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
    dias = [Dia(primeiro + timedelta(days=i), primeiro + timedelta(days=i) >= conta.data_inicio)
            for i in range((ultimo - primeiro).days + 1)]
    ativos = [d for d in dias if d.ativo]
    if not ativos:
        return dias, ativos

    por_data = {d.data: d for d in dias}
    inicio_mes = ativos[0].data
    for fixa in _fixas_ativas(conta):
        for e in eventos_fixa(fixa, inicio_mes, ultimo):
            por_data[e.data].movimentos.append(
                Movimento(fixa.nome, e.valor, fixa.tipo == ContaFixa.RECEITA, "fixa", fixa, e))
    for l in conta.lancamentos.filter(data__gte=inicio_mes, data__lte=ultimo):
        por_data[l.data].movimentos.append(
            Movimento(l.descricao, l.valor, l.tipo == Lancamento.ENTRADA, "lancamento", l))
    for f in faturas(conta, inicio_mes, ultimo):
        por_data[f.vencimento].movimentos.append(Movimento(f.nome, f.total, False, "fatura", f))
    if conferencias:
        for c in conta.conferencias.filter(data__gte=inicio_mes, data__lte=ultimo).order_by("data", "id"):
            por_data[c.data].conferencia = c  # a mais recente do dia prevalece
    return dias, ativos


def extrato_mes(conta, ano, mes):
    """Um registro por dia do mês, com movimentos e saldo esperado ao fim de cada dia.

    Os valores batem com saldo_esperado(): os dois usam simular_mes() e dia_de_guardar().
    """
    dias, ativos = _dias_com_movimentos(conta, ano, mes)
    if not ativos:
        return dias
    inicio_mes = ativos[0].data
    for g in guardados(conta, dias[0].data, dias[-1].data):
        dias[g.data.day - 1].movimentos.append(Movimento(g.nome, g.valor, False, "guardar", g))

    # ponto de partida: fim do dia anterior ao primeiro dia ativo do mês
    if inicio_mes == conta.data_inicio:
        anterior = conta.saldo_inicial
    else:
        anterior = saldo_esperado(conta, inicio_mes - timedelta(days=1)).esperado

    orcamento = {o.data: o for o in simular_mes(conta, ano, mes)}
    for dia in ativos:
        o = orcamento[dia.data]
        dia.movimentos.sort(key=lambda m: (not m.entrada, m.nome))
        dia.entradas = sum((m.valor for m in dia.movimentos if m.entrada), ZERO)
        dia.saidas = sum((m.valor for m in dia.movimentos if not m.entrada), ZERO)
        dia.base, dia.disponivel, dia.gasto, dia.uso = o.base, o.disponivel, o.gasto, o.uso
        dia.inicio = anterior
        dia.esperado = anterior + dia.entradas - dia.saidas - dia.gasto
        if dia.conferencia:
            dia.diferenca = dia.conferencia.saldo_real - dia.esperado
        anterior = dia.esperado
    return dias


# Escala divergente do saldo esperado: verde (positivo) ↔ cinza (zero) ↔ laranja (negativo).
NIVEIS = 4


def nivel_cor(valor, maximo):
    """Nível na escala divergente: "z" perto de zero, "p1".."p4" positivo, "n1".."n4" negativo."""
    if maximo == 0 or abs(valor) < maximo * Decimal("0.05"):
        return "z"
    nivel = min(NIVEIS, max(1, math.ceil(abs(valor) / maximo * NIVEIS)))
    return f"{'p' if valor > 0 else 'n'}{nivel}"


def aplicar_escala(dias):
    """Define dia.cor ("p1".."p4", "z", "n1".."n4") e devolve a legenda.

    A intensidade é relativa ao maior saldo (em valor absoluto) do mês, com a mesma
    escala para os dois lados, então valores do mesmo tamanho têm a mesma força.
    """
    ativos = [d for d in dias if d.ativo]
    maximo = max((abs(d.esperado) for d in ativos), default=ZERO)
    for d in dias:
        d.cor = nivel_cor(d.esperado, maximo) if d.ativo else ""

    passo = maximo / NIVEIS
    return {
        "maximo": maximo,
        "negativos": [{"cor": f"n{k}", "ate": -passo * k} for k in range(NIVEIS, 0, -1)],
        "positivos": [{"cor": f"p{k}", "ate": passo * k} for k in range(1, NIVEIS + 1)],
        "tem_negativo": any(d.esperado < 0 for d in ativos),
    }


def meses_do_seletor(conta, ano_inicial, ano_final, hoje):
    """Meses de ano_inicial a ano_final com o saldo de fechamento e a cor dele.

    Devolve [{"ano": 2026, "meses": [{"data", "ativo", "fim", "cor", "futuro"}, ...]}, ...].
    """
    anos = []
    for ano in range(ano_inicial, ano_final + 1):
        meses = []
        for mes in range(1, 13):
            primeiro = date(ano, mes, 1)
            ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
            ativo = ultimo >= conta.data_inicio
            meses.append({
                "data": primeiro,
                "ativo": ativo,
                "fim": saldo_esperado(conta, ultimo).esperado if ativo else None,
                "futuro": primeiro > hoje,
                "aberto": primeiro <= hoje <= ultimo,
            })
        anos.append({"ano": ano, "meses": meses})

    ativos = [m for a in anos for m in a["meses"] if m["ativo"]]
    maximo = max((abs(m["fim"]) for m in ativos), default=ZERO)
    for m in ativos:
        m["cor"] = nivel_cor(m["fim"], maximo)
    return anos


# ---------------------------------------------------------------------------
# Cartão de crédito
#
# Compras feitas antes do dia de fechamento entram na fatura daquele mês; no dia do
# fechamento ou depois, na do mês seguinte. A fatura vence no mesmo mês se o dia de
# vencimento for depois do fechamento; senão, no mês seguinte. A parcela N cai N-1
# faturas depois da primeira. O dinheiro sai da conta no vencimento da fatura.
# ---------------------------------------------------------------------------

def _dia_no_mes(ano, mes, dia):
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def _somar_meses(ano, mes, n):
    indice = ano * 12 + (mes - 1) + n
    return indice // 12, indice % 12 + 1


def primeira_fatura(cartao, data_compra):
    """(ano, mês) de referência da fatura em que a compra (ou a 1ª parcela) entra."""
    fechamento = _dia_no_mes(data_compra.year, data_compra.month, cartao.dia_fechamento)
    if data_compra < fechamento:
        return data_compra.year, data_compra.month
    return _somar_meses(data_compra.year, data_compra.month, 1)


def datas_da_fatura(cartao, ano, mes):
    """(fechamento, vencimento) da fatura de referência ano/mês."""
    fechamento = _dia_no_mes(ano, mes, cartao.dia_fechamento)
    if cartao.dia_vencimento > cartao.dia_fechamento:
        return fechamento, _dia_no_mes(ano, mes, cartao.dia_vencimento)
    ano_v, mes_v = _somar_meses(ano, mes, 1)
    return fechamento, _dia_no_mes(ano_v, mes_v, cartao.dia_vencimento)


@dataclass
class Parcela:
    compra: object
    numero: int
    valor: Decimal
    fechamento: date
    vencimento: date

    @property
    def rotulo(self):
        return f"{self.numero}/{self.compra.parcelas}"


@dataclass
class Cobranca:
    """Uma cobrança mensal de assinatura, já na fatura em que entra."""
    assinatura: object
    data: date
    valor: Decimal
    fechamento: date
    vencimento: date


@dataclass
class Fatura:
    cartao: object
    ano: int
    mes: int
    fechamento: date
    vencimento: date
    parcelas: list = field(default_factory=list)
    cobrancas: list = field(default_factory=list)

    @property
    def total(self):
        return (sum((p.valor for p in self.parcelas), ZERO)
                + sum((c.valor for c in self.cobrancas), ZERO))

    @property
    def quantidade(self):
        return len(self.parcelas) + len(self.cobrancas)

    # mesmos nomes de uma conta fixa, para listas de vencimentos
    @property
    def nome(self):
        return f"Fatura {self.cartao.nome}"

    @property
    def valor(self):
        return self.total

    tipo = "despesa"


def cronograma(compra):
    """Todas as parcelas de uma compra, com fechamento e vencimento de cada fatura."""
    ano, mes = primeira_fatura(compra.cartao, compra.data_compra)
    parcelas = []
    for i, valor in enumerate(compra.valores_parcelas()):
        fechamento, vencimento = datas_da_fatura(compra.cartao, *_somar_meses(ano, mes, i))
        parcelas.append(Parcela(compra, i + 1, valor, fechamento, vencimento))
    return parcelas


def cobrancas(assinatura, inicio, fim):
    """Cobranças da assinatura cujas faturas vencem entre inicio e fim."""
    cartao = assinatura.cartao
    resultado = []
    # uma cobrança vence no máximo ~2 meses depois de feita: começa um pouco antes
    ano, mes = _somar_meses(inicio.year, inicio.month, -2)
    while (ano, mes) <= (fim.year, fim.month):
        data = _dia_no_mes(ano, mes, assinatura.dia_cobranca)
        if assinatura.ativa_em(data):
            fechamento, vencimento = datas_da_fatura(cartao, *primeira_fatura(cartao, data))
            if inicio <= vencimento <= fim:
                resultado.append(Cobranca(assinatura, data, assinatura.valor, fechamento, vencimento))
        ano, mes = _somar_meses(ano, mes, 1)
    return resultado


def faturas(conta, inicio, fim, sem_compra=None):
    """Faturas dos cartões da conta que vencem entre inicio e fim, em ordem de vencimento.

    sem_compra: pk de uma compra a ignorar (para projetar a edição dela).
    """
    por_chave = {}

    def fatura_de(cartao, fechamento, vencimento):
        chave = (cartao.pk, fechamento)
        if chave not in por_chave:
            por_chave[chave] = Fatura(cartao, fechamento.year, fechamento.month, fechamento, vencimento)
        return por_chave[chave]

    compras = CompraCartao.objects.filter(cartao__conta=conta).select_related("cartao")
    if sem_compra:
        compras = compras.exclude(pk=sem_compra)
    for compra in compras:
        for p in cronograma(compra):
            if inicio <= p.vencimento <= fim:
                fatura_de(compra.cartao, p.fechamento, p.vencimento).parcelas.append(p)
    for assinatura in AssinaturaCartao.objects.filter(cartao__conta=conta).select_related("cartao"):
        for c in cobrancas(assinatura, inicio, fim):
            fatura_de(assinatura.cartao, c.fechamento, c.vencimento).cobrancas.append(c)

    for f in por_chave.values():
        f.parcelas.sort(key=lambda p: (p.compra.data_compra, p.compra.pk))
        f.cobrancas.sort(key=lambda c: (c.data, c.assinatura.descricao))
    return sorted(por_chave.values(), key=lambda f: (f.vencimento, f.cartao.nome))


def proximo_fechamento(cartao, hoje):
    """(fechamento, vencimento) da próxima fatura a fechar, a partir de hoje (inclusive)."""
    ano, mes = hoje.year, hoje.month
    fechamento, vencimento = datas_da_fatura(cartao, ano, mes)
    if fechamento < hoje:
        fechamento, vencimento = datas_da_fatura(cartao, *_somar_meses(ano, mes, 1))
    return fechamento, vencimento


def serie_diaria(conta, inicio, fim):
    """Saldo esperado ao fim de cada dia entre inicio e fim (None antes do início do cálculo)."""
    valores = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        for d in extrato_mes(conta, ano, mes):
            if inicio <= d.data <= fim:
                valores.append(d.esperado if d.ativo else None)
        ano, mes = _somar_meses(ano, mes, 1)
    return valores


# ---------------------------------------------------------------------------
# Guardar todo mês
#
# O valor guardado sai da conta no dia de maior saldo do mês. Para achar esse dia
# bastam as movimentações do próprio mês: o saldo com que o mês começa soma a mesma
# quantia a todos os dias e não muda qual deles é o maior. Empate: o primeiro dia.
# ---------------------------------------------------------------------------

@dataclass
class Guardar:
    data: date
    valor: Decimal
    confirmado: bool = False      # a pessoa informou que guardou (valor e data reais)
    avulso: bool = False
    aporte: object = None         # Aporte, quando confirmado ou avulso
    previsto: Decimal = None      # valor que estava previsto para o mês

    @property
    def nome(self):
        if self.avulso:
            return "Guardado avulso" + (f": {self.aporte.descricao}" if self.aporte.descricao else "")
        return "Guardado no mês" if self.confirmado else "Guardar no mês"


def _aportes(conta):
    """Aportes da conta, carregados uma vez por instância (mensais por mês e avulsos)."""
    if "_cache_aportes" not in conta.__dict__:
        mensais, avulsos = {}, []
        for a in conta.aportes.all():
            if a.tipo == a.MENSAL:
                mensais[(a.referencia.year, a.referencia.month)] = a
            else:
                avulsos.append(a)
        conta.__dict__["_cache_aportes"] = (mensais, avulsos)
    return conta.__dict__["_cache_aportes"]


def dia_de_guardar(conta, ano, mes):
    """Quanto e quando guardar no mês: a confirmação, se houver; senão a previsão.

    A previsão é o valor configurado no dia de maior saldo do mês (None se não houver).
    """
    cache = conta.__dict__.setdefault("_cache_guardar", {})
    if (ano, mes) in cache:
        return cache[(ano, mes)]

    previsto_valor = conta.guardar_mensal
    confirmado = _aportes(conta)[0].get((ano, mes))
    if confirmado:
        resultado = Guardar(confirmado.data, confirmado.valor, confirmado=True, aporte=confirmado,
                            previsto=previsto_valor)
    elif previsto_valor <= 0:
        resultado = None
    else:
        _, ativos = _dias_com_movimentos(conta, ano, mes, conferencias=False)
        resultado = None
        if ativos:
            gasto = {o.data: o.gasto for o in simular_mes(conta, ano, mes)}
            acumulado, maior = ZERO, None
            for d in ativos:
                entradas = sum((m.valor for m in d.movimentos if m.entrada), ZERO)
                saidas = sum((m.valor for m in d.movimentos if not m.entrada), ZERO)
                acumulado += entradas - saidas - gasto[d.data]
                if maior is None or acumulado > maior:
                    maior, resultado = acumulado, Guardar(d.data, previsto_valor, previsto=previsto_valor)
    cache[(ano, mes)] = resultado
    return resultado


def guardados(conta, inicio, fim):
    """Tudo o que foi (ou será) guardado entre inicio e fim: meses e avulsos, por data."""
    mensais, avulsos = _aportes(conta)
    if conta.guardar_mensal <= 0 and not mensais and not avulsos:
        return []
    inicio = max(inicio, conta.data_inicio)
    if fim < inicio:
        return []
    resultado = []
    # a confirmação de um mês pode ter outra data (ex.: guardou o de outubro em 02/11)
    ano, mes = _somar_meses(inicio.year, inicio.month, -1)
    ano_fim, mes_fim = _somar_meses(fim.year, fim.month, 1)
    while (ano, mes) <= (ano_fim, mes_fim):
        if date(ano, mes, calendar.monthrange(ano, mes)[1]) >= conta.data_inicio:
            g = dia_de_guardar(conta, ano, mes)
            if g and inicio <= g.data <= fim:
                resultado.append(g)
        ano, mes = _somar_meses(ano, mes, 1)
    resultado += [Guardar(a.data, a.valor, confirmado=True, avulso=True, aporte=a)
                  for a in avulsos if inicio <= a.data <= fim]
    resultado.sort(key=lambda g: (g.data, g.avulso))
    return resultado
