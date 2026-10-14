from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import importacao, insights, rendimento
from .calculos import extrato_mes, saldo_esperado, totais_do_mes
from .models import Aporte, Conta, ContaFixa, Familia, Objetivo, Resgate
from .tests import cadastrar


class ResgateCalculoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(familia=Familia.objects.create(nome="T"), nome="Nubank",
                                          saldo_inicial=Decimal("1000"), data_inicio=date(2026, 1, 1),
                                          gasto_mensal=Decimal("0"))

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_entra_na_conta_como_recebimento(self):
        Resgate.objects.create(conta=self.conta, data=date(2026, 1, 10), valor=Decimal("500"), motivo="Conserto")
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 1, 9)).esperado, Decimal("1000"))
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 1, 10)).esperado, Decimal("1500"))
        dias = extrato_mes(self.nova(), 2026, 1)
        [m] = dias[9].movimentos
        self.assertEqual((m.nome, m.valor, m.entrada, m.origem), ("Resgate: Conserto", Decimal("500"), True, "resgate"))
        self.assertEqual(totais_do_mes(dias)["recebido"], Decimal("500"))
        for d in dias:   # extrato e saldo esperado continuam batendo dia a dia
            self.assertEqual(d.esperado, saldo_esperado(self.nova(), d.data).esperado, d.data)

    def test_sai_dos_investimentos_e_do_objetivo(self):
        reserva = Objetivo.objects.create(conta=self.conta, nome="Reserva", valor_alvo=10000, valor_inicial=Decimal("2000"))
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 1, 5), valor=Decimal("1000"),
                              objetivo=reserva)
        Resgate.objects.create(conta=self.conta, data=date(2026, 1, 10), valor=Decimal("300"), motivo="Saúde",
                               objetivo=reserva)
        from .views import _investimentos
        inv = _investimentos(self.nova(), date(2026, 1, 20))
        self.assertEqual([(l["situacao"], l["valor"], l["acumulado"]) for l in inv["linhas"]],
                         [("avulso", Decimal("1000"), Decimal("1000")), ("resgate", Decimal("-300"), Decimal("700"))])
        self.assertEqual((inv["total_hoje"], inv["resgatado"]), (Decimal("700"), Decimal("300")))
        self.assertEqual(reserva.guardado, Decimal("2700"))   # 2000 + 1000 − 300

    def test_carteira_deixa_de_render_o_que_saiu(self):
        conta = self.nova()
        conta.investido_inicial, conta.investido_em, conta.rendimento_anual = Decimal("10000"), date(2026, 1, 1), Decimal("12")
        conta.save()
        Resgate.objects.create(conta=conta, data=date(2026, 7, 2), valor=Decimal("1000"), motivo="Viagem")
        esperado = 10000 * 1.12 - 1000 * 1.12 ** (183 / 365)
        self.assertEqual(rendimento.valor_esperado(self.nova(), date(2027, 1, 1)), Decimal(str(round(esperado, 2))))
        julho = next(l for l in rendimento.carteira(self.nova(), date(2026, 8, 1))["meses"] if l["mes"] == date(2026, 7, 1))
        self.assertEqual((julho["entrou"], julho["saiu"]), (Decimal("0"), Decimal("1000")))

    def test_poupanca_nao_conta_o_resgate_como_renda(self):
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("5000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        Aporte.objects.create(conta=self.conta, tipo=Aporte.MENSAL, referencia=date(2026, 1, 1),
                              data=date(2026, 1, 6), valor=Decimal("1000"))
        Resgate.objects.create(conta=self.conta, data=date(2026, 1, 20), valor=Decimal("400"), motivo="Emergência")
        jan = insights.historico_poupanca(self.nova(), date(2026, 2, 10))["linhas"][0]
        self.assertEqual((jan["recebido"], jan["guardado"], jan["resgatado"], jan["taxa"]),
                         (Decimal("5000"), Decimal("600"), Decimal("400"), Decimal("12.0")))
        f = insights.fechamento(self.nova(), date(2026, 1, 1), date(2026, 2, 10), {})
        self.assertIn("resgate", [s["tipo"] for s in f["sugestoes"]])


class ResgateTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.conta = Conta.objects.create(familia=User.objects.get(username="arthur").membro.familia, nome="Nubank",
                                          saldo_inicial=Decimal("1000"), data_inicio=date.today() - timedelta(days=40),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("300"))
        self.url = reverse("financas:resgate_novo", args=[self.conta.pk])
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def test_registrar_ver_e_excluir(self):
        self.assertContains(self.client.get(self.painel), 'id="resgate"')
        hoje = date.today()
        r = self.client.post(self.url, {"valor": "250", "data": hoje.isoformat(), "motivo": "  Conta  do hospital "})
        self.assertRedirects(r, self.painel + "#resgate", fetch_redirect_response=False)
        res = Resgate.objects.get()
        self.assertEqual((res.valor, res.motivo), (Decimal("250"), "Conta do hospital"))
        p = self.client.get(self.painel + f"?dia={hoje.isoformat()}")
        self.assertContains(p, "Resgate: Conta do hospital")   # no extrato do dia
        self.assertContains(p, "tirado dos investimentos")
        self.assertContains(p, "R$ 250,00 tirado")             # no resumo de investimentos
        self.client.post(reverse("financas:resgate_excluir", args=[res.pk]))
        self.assertFalse(Resgate.objects.exists())

    def test_validacoes(self):
        antes = (self.conta.data_inicio - timedelta(days=1)).isoformat()
        for dados, erro in [({"valor": "100", "data": date.today().isoformat(), "motivo": ""}, "Informe o motivo"),
                            ({"valor": "0", "data": date.today().isoformat(), "motivo": "x"}, "maior que zero"),
                            ({"valor": "10", "data": antes, "motivo": "x"}, "anterior ao início")]:
            self.assertContains(self.client.post(self.url, dados, follow=True), erro)
        self.assertFalse(Resgate.objects.exists())

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="X"), nome="Deles", saldo_inicial=0,
                                     data_inicio=date.today())
        r = Resgate.objects.create(conta=outra, data=date.today(), valor=1, motivo="x")
        self.assertEqual(self.client.post(reverse("financas:resgate_novo", args=[outra.pk]),
                                          {"valor": "1", "data": date.today().isoformat(), "motivo": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:resgate_excluir", args=[r.pk])).status_code, 404)

    def test_importar_reconhece_resgate(self):
        dia = date.today() - timedelta(days=2)
        ts = [importacao.Transacao(dia, "RESGATE CDB LIQUIDEZ", Decimal("800"), "ofx:R1")]
        importacao.sugerir(self.conta, ts, date.today())
        self.assertEqual(ts[0].acao, "resgate")
        importacao.aplicar(self.conta, ts, {0: ("resgate", None)}, {}, date.today())
        self.assertEqual(Resgate.objects.get().valor, Decimal("800"))
        importacao.sugerir(Conta.objects.get(pk=self.conta.pk), ts, date.today())
        self.assertEqual(ts[0].acao, "ignorar")   # importar de novo não duplica
