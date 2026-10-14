from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import rendimento
from .models import Aporte, ConferenciaInvestimento, Conta, Familia
from .tests import cadastrar


class RendimentoCalculoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(
            familia=Familia.objects.create(nome="T"), nome="Nubank", saldo_inicial=Decimal("1000"),
            data_inicio=date(2026, 1, 1), investido_inicial=Decimal("10000"), investido_em=date(2026, 1, 1),
            rendimento_anual=Decimal("12"))

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_juros_compostos_por_dia(self):
        self.assertEqual(rendimento.valor_esperado(self.nova(), date(2027, 1, 1)), Decimal("11200.00"))   # 365 dias
        self.assertEqual(rendimento.valor_esperado(self.nova(), date(2026, 1, 1)), Decimal("10000.00"))

    def test_aporte_rende_so_a_partir_do_dia_em_que_entrou(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 7, 2), valor=Decimal("1000"))
        esperado = 10000 * 1.12 + 1000 * 1.12 ** (183 / 365)
        self.assertEqual(rendimento.valor_esperado(self.nova(), date(2027, 1, 1)), Decimal(str(round(esperado, 2))))

    def test_guardado_antes_do_saldo_informado_nao_conta_de_novo(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 1, 1), valor=Decimal("500"))
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 1, 2), valor=Decimal("300"))
        nomes = [n for _, _, n in rendimento.entradas(self.nova(), date(2026, 2, 1))]
        self.assertEqual(nomes, ["Saldo informado", "Guardado avulso"])

    def test_sem_rendimento_e_so_a_soma(self):
        conta = self.nova()
        conta.rendimento_anual = None
        Aporte.objects.create(conta=conta, tipo=Aporte.AVULSO, data=date(2026, 3, 1), valor=Decimal("250"))
        self.assertEqual(rendimento.valor_esperado(conta, date(2027, 6, 1)), Decimal("10250.00"))

    def test_situacao(self):
        self.assertEqual(rendimento.situacao(Decimal("-50"), Decimal("10000")), "dentro")   # 0,5%
        self.assertEqual(rendimento.situacao(Decimal("-150"), Decimal("10000")), "abaixo")
        self.assertEqual(rendimento.situacao(Decimal("150"), Decimal("10000")), "acima")
        self.assertEqual(rendimento.situacao(Decimal("-4"), Decimal("100")), "dentro")      # folga mínima de R$ 5

    def test_mes_a_mes_fecha_a_conta(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 2, 10), valor=Decimal("800"))
        ConferenciaInvestimento.objects.create(conta=self.conta, data=date(2026, 2, 28), valor=Decimal("11000"))
        c = rendimento.carteira(self.nova(), date(2026, 3, 15))
        mar, fev, jan = c["meses"]
        self.assertTrue(mar["parcial"])
        self.assertEqual(fev["entrou"], Decimal("800"))
        self.assertEqual(jan["entrou"], Decimal("10000"))   # o saldo informado entra no mês dele
        for linha, anterior in [(fev, jan), (mar, fev)]:
            self.assertEqual(linha["esperado"], anterior["esperado"] + linha["entrou"] + linha["rendeu"])
        self.assertGreater(fev["rendeu"], 0)
        self.assertEqual(fev["conferencia"]["situacao"], "dentro")
        self.assertEqual(c["aplicado"], Decimal("10800"))
        self.assertEqual(c["rendimento_ate_hoje"], c["esperado_hoje"] - Decimal("10800"))

    def test_nao_configurada(self):
        conta = Conta.objects.create(familia=self.conta.familia, nome="Outra", saldo_inicial=0, data_inicio=date(2026, 1, 1))
        self.assertIsNone(rendimento.carteira(conta, date(2026, 3, 1)))


class CarteiraTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.conta = Conta.objects.create(familia=User.objects.get(username="arthur").membro.familia, nome="Nubank",
                                          saldo_inicial=Decimal("1000"), data_inicio=date.today() - timedelta(days=60))
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def test_configurar_e_conferir(self):
        r = self.client.get(self.painel)
        self.assertContains(r, "Seu dinheiro investido")
        self.assertContains(r, 'name="rendimento_anual"')
        dia = date.today() - timedelta(days=30)
        self.client.post(reverse("financas:carteira_salvar", args=[self.conta.pk]),
                         {"investido_inicial": "20000", "investido_em": dia.isoformat(), "rendimento_anual": "10.5"})
        conta = Conta.objects.get(pk=self.conta.pk)
        self.assertEqual((conta.investido_inicial, conta.investido_em, conta.rendimento_anual),
                         (Decimal("20000"), dia, Decimal("10.5")))
        esperado = rendimento.valor_esperado(conta, date.today())
        self.assertGreater(esperado, Decimal("20000"))
        r = self.client.post(reverse("financas:investimento_conferir", args=[self.conta.pk]),
                             {"valor": str(esperado - 30), "data": date.today().isoformat()}, follow=True)
        self.assertContains(r, "dentro do esperado")
        r = self.client.post(reverse("financas:investimento_conferir", args=[self.conta.pk]),
                             {"valor": "15000", "data": date.today().isoformat()}, follow=True)
        self.assertContains(r, "abaixo do esperado")
        self.assertContains(r, "Deveria ter hoje")
        c = ConferenciaInvestimento.objects.first()
        self.client.post(reverse("financas:investimento_conferencia_excluir", args=[c.pk]))
        self.assertEqual(ConferenciaInvestimento.objects.count(), 1)

    def test_validacoes(self):
        url = reverse("financas:carteira_salvar", args=[self.conta.pk])
        amanha = (date.today() + timedelta(days=1)).isoformat()
        for dados, erro in [({"investido_inicial": "-1"}, "negativo"),
                            ({"investido_inicial": "100", "investido_em": amanha}, "hoje ou do passado"),
                            ({"investido_inicial": "100", "rendimento_anual": "150"}, "entre 0% e 100%"),
                            ({"investido_em": date.today().isoformat()}, "quanto havia investido")]:
            self.assertContains(self.client.post(url, dados, follow=True), erro)
        self.assertIsNone(Conta.objects.get(pk=self.conta.pk).investido_inicial)
        self.client.post(url, {"investido_inicial": "500"})       # sem data: o saldo é de hoje
        self.assertEqual(Conta.objects.get(pk=self.conta.pk).investido_em, date.today())
        r = self.client.post(reverse("financas:investimento_conferir", args=[self.conta.pk]),
                             {"valor": "100", "data": amanha}, follow=True)
        self.assertContains(r, "hoje ou de dias passados")

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="X"), nome="Deles", saldo_inicial=0,
                                     data_inicio=date.today())
        c = ConferenciaInvestimento.objects.create(conta=outra, data=date.today(), valor=1)
        self.assertEqual(self.client.post(reverse("financas:carteira_salvar", args=[outra.pk]),
                                          {"investido_inicial": "1"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:investimento_conferencia_excluir", args=[c.pk])).status_code, 404)
