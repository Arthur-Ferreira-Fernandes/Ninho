"""Importar o extrato da conta (OFX ou CSV exportado pelo banco).

O extrato traz coisas que o Ninho já prevê (salário, contas fixas, fatura do cartão, o guardado
do mês). Cada transação é comparada com o planejamento para não contar nada duas vezes:
o que já está previsto é ignorado, contas variáveis viram o pagamento real do mês, aplicações
confirmam o guardado e o resto vira gasto do dia ou lançamento.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .calculos import ZERO, dia_de_guardar, eventos_fixa, faturas
from .categorias import normalizar
from .models import Aporte, ContaFixa, GastoDia, Lancamento, PagamentoConta

TAMANHO_MAXIMO = 2 * 1024 * 1024


class ErroImportacao(Exception):
    pass


@dataclass
class Transacao:
    data: date
    descricao: str
    valor: Decimal          # negativo: saiu da conta; positivo: entrou
    id_externo: str
    acao: str = ""          # o que fazer (ver ACOES)
    motivo: str = ""        # por que essa sugestão
    categoria_id: int = None
    opcoes: list = field(default_factory=list)   # ações possíveis para esta transação: [(valor, rótulo)]

    @property
    def saida(self):
        return self.valor < 0

    @property
    def absoluto(self):
        return abs(self.valor)

    def para_sessao(self):
        return {"data": self.data.isoformat(), "descricao": self.descricao, "valor": str(self.valor),
                "id_externo": self.id_externo}

    @classmethod
    def da_sessao(cls, d):
        return cls(date.fromisoformat(d["data"]), d["descricao"], Decimal(d["valor"]), d["id_externo"])


# ---------------------------------------------------------------------------
# Leitura dos arquivos
# ---------------------------------------------------------------------------

def _decodificar(conteudo):
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1")


def _numero(texto):
    """'1.234,56' / '1,234.56' / '-45.90' / 'R$ 12,00' -> Decimal."""
    t = re.sub(r"[^\d,.\-+]", "", (texto or "").strip())
    if not t:
        raise ValueError(texto)
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValueError(texto)


def _data(texto):
    t = (texto or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(t[:10] if formato != "%Y%m%d" else t[:8], formato).date()
        except ValueError:
            continue
    raise ValueError(texto)


def ler_ofx(texto):
    transacoes = []
    for bloco in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", texto, re.S | re.I):
        campos = {m.group(1).upper(): m.group(2).strip()
                  for m in re.finditer(r"<(\w+)>([^<\r\n]*)", bloco)}
        try:
            data = _data(campos.get("DTPOSTED", ""))
            valor = _numero(campos.get("TRNAMT", ""))
        except ValueError:
            continue
        descricao = " ".join(filter(None, [campos.get("NAME", ""), campos.get("MEMO", "")])) or "Transação"
        id_externo = campos.get("FITID") or _hash(data, descricao, valor, len(transacoes))
        transacoes.append(Transacao(data, " ".join(descricao.split())[:200], valor, f"ofx:{id_externo}"[:80]))
    return transacoes


COLUNAS = {
    "data": ["data", "date", "data lancamento", "data do lancamento", "data movimento", "dt"],
    "descricao": ["descricao", "historico", "lancamento", "title", "titulo", "estabelecimento", "detalhes",
                  "description", "memo"],
    "valor": ["valor", "amount", "value", "valor (r$)", "quantia"],
    "id": ["identificador", "id", "fitid"],
}


def _achar_colunas(cabecalho):
    nomes = [normalizar(c) for c in cabecalho]
    achadas = {}
    for chave, opcoes in COLUNAS.items():
        for i, nome in enumerate(nomes):
            if nome in opcoes or any(nome.startswith(o + " ") for o in opcoes):
                achadas.setdefault(chave, i)
    return achadas


def ler_csv(texto):
    amostra = texto[:4096]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=";,\t")
    except csv.Error:
        class dialeto(csv.excel):
            delimiter = ";" if amostra.count(";") > amostra.count(",") else ","
    linhas = [l for l in csv.reader(io.StringIO(texto), dialeto) if any(c.strip() for c in l)]
    # o cabeçalho pode não ser a primeira linha (alguns bancos põem o nome da conta antes)
    for inicio, linha in enumerate(linhas[:15]):
        colunas = _achar_colunas(linha)
        if {"data", "descricao", "valor"} <= set(colunas):
            break
    else:
        raise ErroImportacao("Não encontrei as colunas de data, descrição e valor no CSV. "
                             "Confira se o arquivo é o extrato da conta (não o da fatura do cartão).")
    transacoes = []
    for linha in linhas[inicio + 1:]:
        try:
            data = _data(linha[colunas["data"]])
            valor = _numero(linha[colunas["valor"]])
        except (ValueError, IndexError):
            continue   # linhas de saldo, totais etc.
        descricao = " ".join(linha[colunas["descricao"]].split())[:200] or "Transação"
        if "id" in colunas and colunas["id"] < len(linha) and linha[colunas["id"]].strip():
            id_externo = linha[colunas["id"]].strip()
        else:
            id_externo = _hash(data, descricao, valor, sum(1 for t in transacoes
                                                           if (t.data, t.descricao, t.valor) == (data, descricao, valor)))
        transacoes.append(Transacao(data, descricao, valor, f"csv:{id_externo}"[:80]))
    return transacoes


def _hash(data, descricao, valor, n):
    return hashlib.sha1(f"{data}|{descricao}|{valor}|{n}".encode()).hexdigest()[:24]


def ler_arquivo(nome, conteudo):
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroImportacao("Arquivo grande demais (máximo de 2 MB). Exporte um período menor.")
    texto = _decodificar(conteudo)
    if "<OFX>" in texto.upper() or nome.lower().endswith((".ofx", ".qfx")):
        transacoes = ler_ofx(texto)
    else:
        transacoes = ler_csv(texto)
    if not transacoes:
        raise ErroImportacao("Nenhuma transação encontrada no arquivo.")
    transacoes.sort(key=lambda t: (t.data, t.descricao))
    return transacoes


# ---------------------------------------------------------------------------
# Sugestões: o que fazer com cada transação
# ---------------------------------------------------------------------------

PALAVRAS_GUARDAR = ["aplicacao", "aplic", "poupanca", "investimento", "invest", "cdb", "tesouro", "lci", "lca",
                    "caixinha", "porquinho", "cofrinho", "guardar dinheiro"]
PALAVRAS_FATURA = ["fatura", "pagamento cartao", "pag cartao", "pgto cartao", "cartao de credito"]


def _perto(a, b, tolerancia):
    return abs(a - b) <= max(Decimal("1"), b * tolerancia)


def sugerir(conta, transacoes, hoje):
    """Preenche acao, motivo, categoria e opções de cada transação."""
    ids = set(Lancamento.objects.filter(conta=conta).exclude(id_externo="").values_list("id_externo", flat=True))
    ids |= set(GastoDia.objects.filter(conta=conta).exclude(id_externo="").values_list("id_externo", flat=True))
    if transacoes:
        inicio = min(t.data for t in transacoes) - timedelta(days=10)
        fim = max(t.data for t in transacoes) + timedelta(days=10)
        eventos = [e for f in conta.fixas.filter(ativa=True).prefetch_related("pagamentos")
                   for e in eventos_fixa(f, inicio, fim)]
        lista_faturas = faturas(conta, inicio, fim)
    else:
        eventos, lista_faturas = [], []
    usados = set()
    aportes = set(Aporte.objects.filter(conta=conta).values_list("data", "valor"))
    limite_gasto = conta.orcamento_mensal * Decimal("0.3") if conta.orcamento_mensal > 0 else Decimal("300")

    for t in transacoes:
        texto = normalizar(t.descricao)
        t.opcoes = _opcoes(conta, t)
        if t.data < conta.data_inicio:
            t.acao, t.motivo = "ignorar", "antes do início do cálculo"
            continue
        if t.data > hoje:
            t.acao, t.motivo = "ignorar", "data futura"
            continue
        if t.id_externo in ids:
            t.acao, t.motivo = "ignorar", "já importada antes"
            continue

        # fatura do cartão já prevista
        fatura = next((f for f in lista_faturas if abs((f.vencimento - t.data).days) <= 5
                       and t.saida and _perto(t.absoluto, f.total, Decimal("0.02"))), None)
        if fatura or (t.saida and any(p in texto for p in PALAVRAS_FATURA) and conta.cartoes.exists()):
            t.acao, t.motivo = "ignorar", f"já previsto: {fatura.nome}" if fatura else "pagamento de fatura (já previsto pelo cartão)"
            continue

        # conta fixa / salário previstos
        evento = _evento_parecido(t, eventos, usados)
        if evento:
            usados.add(id(evento))
            fixa = evento.fixa
            if fixa.variavel and not evento.feito:
                t.acao = f"pagar:{fixa.pk}:{evento.vencimento.isoformat()}"
                t.motivo = f"valor real de {fixa.nome} ({evento.vencimento:%m/%Y}); estimativa era {_brl(fixa.valor)}"
            else:
                t.acao, t.motivo = "ignorar", f"já previsto: {fixa.nome}"
            continue

        # dinheiro guardado
        if t.saida and (t.data, t.absoluto) in aportes:
            t.acao, t.motivo = "ignorar", "já registrado em Investimentos"
            continue
        if t.saida and any(re.search(rf"(?<![a-z]){p}", texto) for p in PALAVRAS_GUARDAR):
            mes = t.data.replace(day=1)
            g = dia_de_guardar(conta, mes.year, mes.month)
            if g and not g.confirmado and _perto(t.absoluto, g.valor, Decimal("0.15")):
                t.acao, t.motivo = f"confirmar:{mes.isoformat()}", f"guardado de {mes:%m/%Y} (previsto {_brl(g.valor)})"
            else:
                t.acao, t.motivo = "guardado", "parece uma aplicação"
            continue

        if t.saida:
            t.acao = "gasto" if t.absoluto <= limite_gasto else "saida"
            t.motivo = "gasto do dia a dia" if t.acao == "gasto" else "valor alto: lançamento avulso"
            categoria = conta.familia.sugerir_categoria(t.descricao)
            t.categoria_id = categoria.pk if categoria else None
        else:
            t.acao, t.motivo = "entrada", "entrada não prevista"
    return transacoes


def _evento_parecido(t, eventos, usados):
    """A ocorrência de conta fixa (ou salário) que corresponde à transação, se houver."""
    candidatos = []
    for e in eventos:
        if id(e) in usados or (e.fixa.tipo == ContaFixa.RECEITA) == t.saida:
            continue
        distancia = abs((e.data - t.data).days)
        tolerancia = Decimal("0.4") if e.fixa.variavel else Decimal("0.03")
        if distancia <= 7 and _perto(t.absoluto, e.valor, tolerancia):
            candidatos.append((distancia, abs(t.absoluto - e.valor), e))
    return min(candidatos, key=lambda c: (c[0], c[1]))[2] if candidatos else None


def _opcoes(conta, t):
    if t.saida:
        opcoes = [("gasto", "Gasto do dia a dia"), ("saida", "Saída avulsa (lançamento)"),
                  ("guardado", "Dinheiro guardado (avulso)")]
        for f in conta.fixas.filter(ativa=True, variavel=True, tipo=ContaFixa.DESPESA):
            ref = f.ocorrencia_no_mes(t.data.year, t.data.month)
            opcoes.append((f"pagar:{f.pk}:{ref.isoformat()}", f"Pagamento de {f.nome} ({ref:%m/%Y})"))
        mes = t.data.replace(day=1)
        if conta.guardar_mensal_em(mes) > 0:
            opcoes.append((f"confirmar:{mes.isoformat()}", f"Guardado do mês ({mes:%m/%Y})"))
    else:
        opcoes = [("entrada", "Entrada avulsa (lançamento)")]
    return opcoes + [("ignorar", "Ignorar")]


def _brl(valor):
    from .templatetags.dinheiro import brl
    return brl(valor)


# ---------------------------------------------------------------------------
# Aplicar as escolhas
# ---------------------------------------------------------------------------

def aplicar(conta, transacoes, escolhas, categorias, hoje):
    """Cria o que foi escolhido para cada transação. escolhas: {índice: (ação, categoria_id)}.

    Devolve um resumo {"gasto": n, "saida": n, ...}.
    """
    resumo = {}
    for i, t in enumerate(transacoes):
        acao, categoria_id = escolhas.get(i, ("ignorar", None))
        if acao == "ignorar" or not (conta.data_inicio <= t.data <= hoje):
            continue
        categoria = categorias.get(categoria_id)
        if acao == "gasto" and t.saida:
            GastoDia.objects.create(conta=conta, data=t.data, descricao=t.descricao[:120], valor=t.absoluto,
                                    categoria=categoria or conta.familia.sugerir_categoria(t.descricao),
                                    id_externo=t.id_externo)
        elif acao == "saida" and t.saida:
            Lancamento.objects.create(conta=conta, data=t.data, descricao=t.descricao, valor=t.absoluto,
                                      tipo=Lancamento.SAIDA, id_externo=t.id_externo,
                                      categoria=categoria or conta.familia.sugerir_categoria(t.descricao))
        elif acao == "entrada" and not t.saida:
            Lancamento.objects.create(conta=conta, data=t.data, descricao=t.descricao, valor=t.absoluto,
                                      tipo=Lancamento.ENTRADA, id_externo=t.id_externo)
        elif acao == "guardado" and t.saida:
            Aporte.objects.create(conta=conta, tipo=Aporte.AVULSO, data=t.data, valor=t.absoluto,
                                  descricao=t.descricao[:120], objetivo=conta.objetivos.filter(padrao=True).first())
        elif acao.startswith("confirmar:") and t.saida:
            referencia = date.fromisoformat(acao.split(":", 1)[1])
            Aporte.objects.update_or_create(
                conta=conta, tipo=Aporte.MENSAL, referencia=referencia,
                defaults={"data": t.data, "valor": t.absoluto, "descricao": "",
                          "objetivo": conta.objetivos.filter(padrao=True).first()})
        elif acao.startswith("pagar:") and t.saida:
            _, fixa_pk, ref = acao.split(":")
            fixa = conta.fixas.filter(pk=fixa_pk).first()
            if fixa is None:
                continue
            referencia = date.fromisoformat(ref)
            existente = fixa.pagamentos.filter(referencia__year=referencia.year,
                                               referencia__month=referencia.month).first()
            p = existente or PagamentoConta(fixa=fixa, referencia=referencia)
            p.valor, p.data, p.situacao = t.absoluto, t.data, PagamentoConta.PAGO
            p.save()
        else:
            continue
        chave = acao.split(":")[0]
        resumo[chave] = resumo.get(chave, 0) + 1
    return resumo
