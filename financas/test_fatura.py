from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .calculos import fatura_a_pagar, fatura_do_cartao
from .models import AssinaturaCartao, Cartao, CompraCartao, Conta, Familia
from .tests import cadastrar


class MilhasTests(TestCase):
    def cartao(self, **campos):
        conta = Conta.objects.create(familia=Familia.objects.create(nome="T"), nome="C", saldo_inicial=0,
                                     data_inicio=date(2026, 1, 1))
        return Cartao(conta=conta, nome="Nu", dia_fechamento=1, dia_vencimento=8, **campos)

    def test_por_dolar_e_por_real(self):
        dolar = self.cartao(milhas_taxa=Decimal("2.2"), milhas_moeda=Cartao.POR_DOLAR, cotacao_dolar=Decimal("5"))
        self.assertEqual(dolar.milhas(Decimal("1000")), 440)            # 1000 / 5 × 2,2
        self.assertEqual(dolar.milhas(Decimal("1001")), 440)            # arredonda para baixo
        real = self.cartao(milhas_taxa=Decimal("1.5"), milhas_moeda=Cartao.POR_REAL)
        self.assertEqual(real.milhas(Decimal("1000")), 1500)

    def test_sem_configuracao(self):
        self.assertIsNone(self.cartao().milhas(Decimal("1000")))
        sem_cotacao = self.cartao(milhas_taxa=Decimal("2"), milhas_moeda=Cartao.POR_DOLAR)
        self.assertFalse(sem_cotacao.gera_milhas)
        self.assertIsNone(sem_cotacao.milhas(Decimal("1000")))


class FaturaCalculoTests(TestCase):
    def setUp(self):
        conta = Conta.objects.create(familia=Familia.objects.create(nome="T"), nome="C", saldo_inicial=0,
                                     data_inicio=date(2026, 1, 1))
        # fecha dia 10 e vence dia 17: compras de 10/01 a 09/02 entram na fatura que fecha em 10/02
        self.cartao = Cartao.objects.create(conta=conta, nome="Nu", dia_fechamento=10, dia_vencimento=17)
        CompraCartao.objects.create(cartao=self.cartao, descricao="Antes do fechamento", valor_total=Decimal("100"),
                                    data_compra=date(2026, 2, 9))
        CompraCartao.objects.create(cartao=self.cartao, descricao="No dia do fechamento", valor_total=Decimal("50"),
                                    data_compra=date(2026, 2, 10))
        CompraCartao.objects.create(cartao=self.cartao, descricao="TV", valor_total=Decimal("300"), parcelas=3,
                                    data_compra=date(2026, 1, 15))
        AssinaturaCartao.objects.create(cartao=self.cartao, descricao="Netflix", valor=Decimal("40"), dia_cobranca=5,
                                        inicio=date(2026, 1, 1))

    def itens(self, ano, mes):
        f = fatura_do_cartao(self.cartao, ano, mes)
        return f, sorted([(p.compra.descricao, p.rotulo, p.valor) for p in f.parcelas]), [c.assinatura.descricao for c in f.cobrancas]

    def test_compras_de_cada_fatura(self):
        f, compras, assinaturas = self.itens(2026, 2)
        self.assertEqual((f.fechamento, f.vencimento), (date(2026, 2, 10), date(2026, 2, 17)))
        self.assertEqual(compras, [("Antes do fechamento", "1/1", Decimal("100")), ("TV", "1/3", Decimal("100"))])
        self.assertEqual(assinaturas, ["Netflix"])
        self.assertEqual(f.total, Decimal("240"))
        _, compras, _ = self.itens(2026, 3)
        self.assertEqual(compras, [("No dia do fechamento", "1/1", Decimal("50")), ("TV", "2/3", Decimal("100"))])

    def test_fatura_vazia(self):
        f, compras, _ = self.itens(2025, 6)
        self.assertEqual((compras, f.total, f.vencimento), ([], 0, date(2025, 6, 17)))

    def test_fatura_a_pagar(self):
        self.assertEqual(fatura_a_pagar(self.cartao, date(2026, 2, 12)), (2026, 2))   # fechou dia 10, vence dia 17
        self.assertEqual(fatura_a_pagar(self.cartao, date(2026, 2, 17)), (2026, 2))
        self.assertEqual(fatura_a_pagar(self.cartao, date(2026, 2, 18)), (2026, 3))


class FaturaTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.conta = Conta.objects.create(familia=User.objects.get(username="arthur").membro.familia, nome="Nubank",
                                          saldo_inicial=Decimal("1000"), data_inicio=date(2026, 1, 1))
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=10, dia_vencimento=17,
                                            milhas_taxa=Decimal("2"), milhas_moeda=Cartao.POR_DOLAR,
                                            cotacao_dolar=Decimal("5"), programa="Smiles")
        CompraCartao.objects.create(cartao=self.cartao, descricao="Geladeira", valor_total=Decimal("3000"), parcelas=10,
                                    data_compra=date(2026, 1, 15))

    def test_pagina_da_fatura(self):
        url = reverse("financas:fatura", args=[self.cartao.pk])
        r = self.client.get(url + "?ref=2026-03")
        self.assertContains(r, "Geladeira")
        self.assertContains(r, "parcela 2/10")
        self.assertContains(r, "R$ 300,00")
        self.assertContains(r, "120")                 # 300 / 5 × 2 milhas
        self.assertContains(r, "Smiles")
        self.assertContains(r, 'href="?ref=2026-02"')
        self.assertContains(r, 'href="?ref=2026-04"')
        self.assertEqual(self.client.get(url + "?ref=xx").status_code, 200)   # ref inválida: fatura a pagar

    def test_aba_cartoes_mostra_itens_e_link(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(r, reverse("financas:fatura", args=[self.cartao.pk]))
        self.assertContains(r, "Compras da fatura que vence")

    def test_formulario_do_cartao(self):
        url = reverse("financas:cartao_editar", args=[self.cartao.pk])
        r = self.client.post(url, {"nome": "Roxinho", "dia_fechamento": "10", "dia_vencimento": "17",
                                   "milhas_taxa": "2.5", "milhas_moeda": "dolar", "cotacao_dolar": ""})
        self.assertContains(r, "Informe a cotação do dólar")
        self.client.post(url, {"nome": "Roxinho", "dia_fechamento": "10", "dia_vencimento": "17",
                               "milhas_taxa": "1.5", "milhas_moeda": "real", "programa": "Livelo"})
        self.cartao.refresh_from_db()
        self.assertEqual((self.cartao.milhas_taxa, self.cartao.milhas_moeda, self.cartao.programa),
                         (Decimal("1.5"), Cartao.POR_REAL, "Livelo"))

    def test_compra_recebe_configuracao_de_milhas(self):
        r = self.client.get(reverse("financas:compra_nova", args=[self.conta.pk]))
        [dados] = r.context["cartoes_dados"]
        self.assertEqual((dados["milhas"], dados["por_dolar"], dados["cotacao"], dados["programa"]), (2.0, True, 5.0, "Smiles"))
        self.assertContains(r, 'id="r-milhas"')

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="X"), nome="Deles", saldo_inicial=0,
                                     data_inicio=date(2026, 1, 1))
        c = Cartao.objects.create(conta=outra, nome="Deles", dia_fechamento=1, dia_vencimento=8)
        self.assertEqual(self.client.get(reverse("financas:fatura", args=[c.pk])).status_code, 404)
