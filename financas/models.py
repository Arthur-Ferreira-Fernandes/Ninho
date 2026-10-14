import calendar
import secrets
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.functional import cached_property

# sem 0/O, 1/I/L para o código ser fácil de ditar
ALFABETO_CONVITE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def gerar_codigo_convite():
    return "".join(secrets.choice(ALFABETO_CONVITE) for _ in range(8))


class Familia(models.Model):
    """Grupo de pessoas que compartilham as mesmas contas."""

    nome = models.CharField(max_length=100)
    codigo_convite = models.CharField(max_length=8, unique=True, default=gerar_codigo_convite)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "família"

    def __str__(self):
        return self.nome


class Membro(models.Model):
    """Liga um usuário à sua família. Cada usuário pertence a uma família."""

    ADMIN = "admin"
    MEMBRO = "membro"
    PAPEIS = [(ADMIN, "Administrador"), (MEMBRO, "Membro")]

    familia = models.ForeignKey(Familia, on_delete=models.CASCADE, related_name="membros")
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membro",
    )
    papel = models.CharField(max_length=6, choices=PAPEIS, default=MEMBRO)
    parentesco = models.CharField(max_length=50, blank=True, help_text="Ex.: esposa, filho, mãe.")

    class Meta:
        ordering = ["usuario__first_name", "usuario__username"]

    def __str__(self):
        return self.usuario.get_full_name() or self.usuario.username

    @property
    def is_admin(self):
        return self.papel == self.ADMIN


class PedidoEntrada(models.Model):
    """Pedido de um usuário para entrar numa família; um administrador aprova ou recusa."""

    familia = models.ForeignKey(Familia, on_delete=models.CASCADE, related_name="pedidos")
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pedido_entrada",
    )
    parentesco = models.CharField(max_length=50, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criado_em"]
        verbose_name = "pedido de entrada"
        verbose_name_plural = "pedidos de entrada"

    def __str__(self):
        return f"{self.usuario} → {self.familia}"


class Conta(models.Model):
    """Conta bancária com o ponto de partida do planejamento."""

    familia = models.ForeignKey(Familia, on_delete=models.CASCADE, related_name="contas")
    nome = models.CharField(max_length=100)
    saldo_inicial = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Quanto havia na conta no início da data de início.",
    )
    data_inicio = models.DateField(help_text="A partir de quando o cálculo começa.")

    # Orçamento do dia a dia: definido por mês e dividido pelos dias de cada mês.
    PERCENTUAL = "percentual"
    MENSAL = "mensal"
    MODOS_GASTO = [(PERCENTUAL, "% do salário"), (MENSAL, "Valor fixo por mês")]

    modo_gasto = models.CharField(max_length=10, choices=MODOS_GASTO, default=MENSAL)
    percentual_gasto = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Porcentagem do salário reservada para o dia a dia.",
    )
    gasto_mensal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"),
        help_text="Quanto pode ser gasto no dia a dia durante o mês.",
    )

    # Quanto guardar por mês: sai da conta no dia de maior saldo de cada mês.
    GUARDAR_NADA = "nada"
    GUARDAR_PERCENTUAL = "percentual"
    GUARDAR_FIXO = "fixo"
    MODOS_GUARDAR = [(GUARDAR_NADA, "Não guardar"), (GUARDAR_PERCENTUAL, "% do salário"),
                     (GUARDAR_FIXO, "Valor fixo por mês")]

    guardar_modo = models.CharField(max_length=10, choices=MODOS_GUARDAR, default=GUARDAR_NADA)
    guardar_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    guardar_valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Reserva mínima: abaixo dela o saldo entra em atenção (amarelo → laranja) e,
    # abaixo de 1/4 dela, em "quase acabando" (vermelho, que se intensifica até o negativo).
    RESERVA_AUTO = "auto"
    RESERVA_FIXO = "fixo"
    RESERVA_PERCENTUAL = "percentual"
    MODOS_RESERVA = [(RESERVA_AUTO, "Automática"), (RESERVA_FIXO, "Valor fixo"),
                     (RESERVA_PERCENTUAL, "% do salário")]
    DIAS_RESERVA_PADRAO = 10
    FRACAO_QUASE_ACABANDO = Decimal("0.25")

    reserva_modo = models.CharField(max_length=10, choices=MODOS_RESERVA, default=RESERVA_AUTO)
    reserva_minima = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reserva_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return self.nome

    def reserva_automatica_em(self, data):
        return (self.orcamento_mensal_em(data) * self.DIAS_RESERVA_PADRAO / 30).quantize(Decimal("1"))

    @property
    def reserva_automatica(self):
        return self.reserva_automatica_em(date.today())

    def reserva_em(self, data):
        """Saldo mínimo confortável no mês de `data`, conforme o modo escolhido.

        Em "% do salário" (e na automática, se o dia a dia for % do salário) ela acompanha
        o salário daquele mês: se o salário cai, a margem cai junto.
        """
        if self.reserva_modo == self.RESERVA_FIXO and self.reserva_minima is not None:
            return self.reserva_minima
        if self.reserva_modo == self.RESERVA_PERCENTUAL and self.reserva_percentual:
            return (self.salario_em(data) * self.reserva_percentual / 100).quantize(Decimal("1"))
        return self.reserva_automatica_em(data)

    @property
    def reserva(self):
        return self.reserva_em(date.today())

    @property
    def quase_acabando(self):
        """Abaixo disto começa o vermelho: 1/4 da reserva."""
        return (self.reserva * self.FRACAO_QUASE_ACABANDO).quantize(Decimal("1"))

    def guardar_mensal_em(self, data):
        if self.guardar_modo == self.GUARDAR_PERCENTUAL:
            return (self.salario_em(data) * (self.guardar_percentual or 0) / 100).quantize(Decimal("0.01"))
        if self.guardar_modo == self.GUARDAR_FIXO:
            return self.guardar_valor or Decimal("0")
        return Decimal("0")

    @property
    def guardar_mensal(self):
        return self.guardar_mensal_em(date.today())

    @cached_property
    def pagamentos_salario(self):
        if not self.pk:
            return []
        return list(self.fixas.filter(salario=True, ativa=True).order_by("dia"))

    def salario_em(self, data):
        """Salário do mês de `data`: soma dos pagamentos válidos naquele mês.

        Um salário pode cair em vários dias e mudar com o tempo (aumento ou redução).
        """
        return sum((p.valor for p in self.pagamentos_salario
                    if p.vigente_em(p.ocorrencia_no_mes(data.year, data.month))), Decimal("0"))

    @property
    def salario(self):
        """Salário deste mês."""
        return self.salario_em(date.today())

    def orcamento_mensal_em(self, data):
        if self.modo_gasto == self.PERCENTUAL:
            return self.salario_em(data) * (self.percentual_gasto or 0) / 100
        return self.gasto_mensal

    @property
    def orcamento_mensal(self):
        return self.orcamento_mensal_em(date.today())

    def gasto_diario_em(self, data):
        """Valor diário no mês de `data` (o orçamento daquele mês dividido pelos dias do mês)."""
        dias_no_mes = calendar.monthrange(data.year, data.month)[1]
        return self.orcamento_mensal_em(data) / dias_no_mes

    @property
    def gasto_diario(self):
        return self.gasto_diario_em(date.today())


class ContaFixa(models.Model):
    """Despesa ou receita que se repete todo mês (aluguel, salário, internet...)."""

    DESPESA = "despesa"
    RECEITA = "receita"
    TIPOS = [(DESPESA, "Despesa"), (RECEITA, "Receita")]

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="fixas")
    nome = models.CharField(max_length=100)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    dia = models.PositiveSmallIntegerField(
        help_text="Dia do mês (1-31). Em meses mais curtos usa o último dia.",
    )
    tipo = models.CharField(max_length=7, choices=TIPOS, default=DESPESA)
    ativa = models.BooleanField(default=True)
    salario = models.BooleanField(
        default=False, help_text="Pagamento do salário; a soma deles é a base do % de gasto.",
    )
    variavel = models.BooleanField(
        default=False,
        help_text="O valor muda todo mês (luz, água, mercado): o valor vira uma estimativa "
                  "e cada mês é marcado como pago com o valor real.",
    )

    # Período de validade (opcional): para aumentos/reduções de salário, fim de um aluguel...
    vigente_desde = models.DateField(null=True, blank=True, help_text="Vale a partir desta data.")
    vigente_ate = models.DateField(null=True, blank=True, help_text="Vale até esta data (inclusive).")

    # Frequência: todo mês num dia fixo, ou a cada N dias (produtos do carro a cada 45 dias...).
    MENSAL = "mensal"
    DIAS = "dias"
    FREQUENCIAS = [(MENSAL, "Todo mês"), (DIAS, "A cada X dias")]
    frequencia = models.CharField(max_length=6, choices=FREQUENCIAS, default=MENSAL)
    intervalo_dias = models.PositiveSmallIntegerField(null=True, blank=True)
    proxima = models.DateField(
        null=True, blank=True,
        help_text="A cada X dias: data prevista da próxima vez. Depois de cada compra marcada, "
                  "a contagem recomeça da data real.",
    )

    def vigente_em(self, data):
        return ((self.vigente_desde is None or data >= self.vigente_desde)
                and (self.vigente_ate is None or data <= self.vigente_ate))

    def ocorrencia_no_mes(self, ano, mes):
        """Data em que a conta mensal cai no mês (dias 29-31 viram o último dia)."""
        return date(ano, mes, min(self.dia, calendar.monthrange(ano, mes)[1]))

    @property
    def periodica(self):
        return self.frequencia == self.DIAS

    @property
    def marcavel(self):
        """Pode ser marcada como paga/comprada com o valor real."""
        return self.variavel or self.periodica

    @property
    def valor_mensal(self):
        """Quanto pesa por mês: o valor, ou a média mensal (365 dias ÷ 12) se for a cada X dias."""
        if self.periodica and self.intervalo_dias:
            return (self.valor * 365 / (12 * self.intervalo_dias)).quantize(Decimal("0.01"))
        return self.valor

    class Meta:
        ordering = ["dia", "nome"]
        verbose_name = "conta fixa"
        verbose_name_plural = "contas fixas"

    def __str__(self):
        return f"{self.nome} (dia {self.dia})"


class Lancamento(models.Model):
    """Entrada ou saída avulsa, fora do gasto diário e das contas fixas."""

    ENTRADA = "entrada"
    SAIDA = "saida"
    TIPOS = [(ENTRADA, "Entrada"), (SAIDA, "Saída")]

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="lancamentos")
    data = models.DateField()
    descricao = models.CharField(max_length=200)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    tipo = models.CharField(max_length=7, choices=TIPOS, default=SAIDA)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "lançamento"

    def __str__(self):
        return f"{self.data:%d/%m} {self.descricao}"


class Conferencia(models.Model):
    """Saldo que o banco realmente mostrava em uma data."""

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="conferencias")
    data = models.DateField()
    saldo_real = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "conferência"

    def __str__(self):
        return f"{self.data:%d/%m/%Y}: {self.saldo_real}"


class UsoDiario(models.Model):
    """O que aconteceu com o dinheiro do dia a dia em uma data.

    Dias sem registro contam como "usou tudo". O que sobra (ou falta) é
    redistribuído entre os dias restantes do mês.
    """

    TUDO = "tudo"
    NADA = "nada"
    PARTE = "parte"
    SITUACOES = [(TUDO, "Usei tudo"), (NADA, "Não usei"), (PARTE, "Usei outro valor")]

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="usos")
    data = models.DateField()
    situacao = models.CharField(max_length=5, choices=SITUACOES)
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                help_text="Quanto foi usado, quando a situação é 'outro valor'.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conta", "data"], name="uso_unico_por_dia")]
        ordering = ["data"]
        verbose_name = "uso diário"
        verbose_name_plural = "usos diários"

    def __str__(self):
        return f"{self.data:%d/%m/%Y}: {self.get_situacao_display()}"


class Cartao(models.Model):
    """Cartão de crédito cuja fatura é paga pela conta."""

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="cartoes")
    nome = models.CharField(max_length=60)
    dia_fechamento = models.PositiveSmallIntegerField(
        help_text="Compras a partir deste dia vão para a fatura do mês seguinte.")
    dia_vencimento = models.PositiveSmallIntegerField(help_text="Dia em que a fatura é paga.")

    class Meta:
        ordering = ["nome"]
        verbose_name = "cartão"
        verbose_name_plural = "cartões"

    def __str__(self):
        return self.nome


class CompraCartao(models.Model):
    """Compra no crédito, à vista (1 parcela) ou parcelada."""

    cartao = models.ForeignKey(Cartao, on_delete=models.CASCADE, related_name="compras")
    descricao = models.CharField(max_length=200)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)
    parcelas = models.PositiveSmallIntegerField(default=1)
    data_compra = models.DateField()

    class Meta:
        ordering = ["-data_compra", "-id"]
        verbose_name = "compra no cartão"
        verbose_name_plural = "compras no cartão"

    def __str__(self):
        return f"{self.descricao} ({self.parcelas}x)"

    def valores_parcelas(self):
        """Valor de cada parcela; a primeira absorve os centavos da divisão."""
        base = (self.valor_total / self.parcelas).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
        primeira = self.valor_total - base * (self.parcelas - 1)
        return [primeira] + [base] * (self.parcelas - 1)


class AssinaturaCartao(models.Model):
    """Cobrança mensal recorrente no cartão (streaming, academia, apps...)."""

    cartao = models.ForeignKey(Cartao, on_delete=models.CASCADE, related_name="assinaturas")
    descricao = models.CharField(max_length=120)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    dia_cobranca = models.PositiveSmallIntegerField(
        help_text="Dia do mês em que a cobrança cai no cartão (29-31 viram o último dia em meses curtos).")
    inicio = models.DateField(help_text="Primeira cobrança a partir desta data.")
    fim = models.DateField(null=True, blank=True,
                           help_text="Cancelada a partir desta data: não cobra mais neste dia nem depois.")

    class Meta:
        ordering = ["descricao"]
        verbose_name = "assinatura no cartão"
        verbose_name_plural = "assinaturas no cartão"

    def __str__(self):
        return f"{self.descricao} ({self.cartao})"

    def ativa_em(self, data):
        return self.inicio <= data and (self.fim is None or data < self.fim)


class PagamentoConta(models.Model):
    """O que aconteceu com uma ocorrência da conta fixa (um mês, ou uma vez da periódica).

    pago: saiu (ou entrou) de fato, com valor e data reais; nas contas comuns é o ajuste do mês.
    remarcado: ainda não aconteceu, mas vai em outra data/valor (continua previsto).
    pulado: não acontece nesta ocorrência.
    """

    PAGO = "pago"
    REMARCADO = "remarcado"
    PULADO = "pulado"
    SITUACOES = [(PAGO, "Pago"), (REMARCADO, "Remarcado"), (PULADO, "Pulado")]

    fixa = models.ForeignKey(ContaFixa, on_delete=models.CASCADE, related_name="pagamentos")
    referencia = models.DateField(help_text="Data de vencimento da ocorrência que foi paga.")
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    data = models.DateField(help_text="Quando o dinheiro saiu (ou entrou) de fato.")
    situacao = models.CharField(max_length=9, choices=SITUACOES, default=PAGO)

    class Meta:
        ordering = ["-referencia"]
        constraints = [models.UniqueConstraint(fields=["fixa", "referencia"], name="um_pagamento_por_ocorrencia")]
        verbose_name = "pagamento de conta"
        verbose_name_plural = "pagamentos de contas"

    def __str__(self):
        return f"{self.fixa.nome} {self.referencia:%m/%Y}: {self.valor}"

    @property
    def feito(self):
        return self.situacao == self.PAGO

    @property
    def pulado(self):
        return self.situacao == self.PULADO


class Aporte(models.Model):
    """Dinheiro guardado de fato: a confirmação do mês ou um valor avulso."""

    MENSAL = "mensal"   # confirma quanto foi guardado no mês (substitui a previsão)
    AVULSO = "avulso"   # valor extra, em qualquer dia
    TIPOS = [(MENSAL, "Guardado do mês"), (AVULSO, "Guardado avulso")]

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="aportes")
    tipo = models.CharField(max_length=6, choices=TIPOS)
    referencia = models.DateField(null=True, blank=True,
                                  help_text="Para o guardado do mês: o 1º dia do mês a que se refere.")
    data = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["data", "id"]
        constraints = [models.UniqueConstraint(fields=["conta", "referencia"], condition=models.Q(tipo="mensal"),
                                               name="um_guardado_por_mes")]
        verbose_name = "valor guardado"
        verbose_name_plural = "valores guardados"

    def __str__(self):
        return f"{self.get_tipo_display()} {self.data:%d/%m/%Y}: {self.valor}"


class GastoDia(models.Model):
    """Um gasto do dinheiro do dia a dia, para detalhar o que foi usado no dia.

    Quando um dia tem gastos lançados, a soma deles é o que foi usado naquele dia.
    """

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="gastos_dia")
    data = models.DateField()
    descricao = models.CharField(max_length=120, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["data", "id"]
        verbose_name = "gasto do dia"
        verbose_name_plural = "gastos do dia"

    def __str__(self):
        return f"{self.data:%d/%m} {self.descricao or 'gasto'}: {self.valor}"
