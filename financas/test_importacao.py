from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import importacao
from .models import Aporte, Cartao, CompraCartao, Conta, ContaFixa, GastoDia, Lancamento, PagamentoConta
from .tests import cadastrar

OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261005120000[-3:BRT]
<TRNAMT>-45.90
<FITID>A1
<MEMO>Compra no debito - PADARIA DO ZE
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261006
<TRNAMT>1500,00
<FITID>A2
<NAME>PIX RECEBIDO
<MEMO>Maria
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

OFX_XML = b"""<?xml version="1.0"?><OFX><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261007</DTPOSTED><TRNAMT>-12.00</TRNAMT><FITID>X9</FITID><MEMO>Uber</MEMO></STMTTRN>
</BANKTRANLIST></OFX>"""


class LeituraTests(SimpleTestCase):
    def test_ofx_sgml_sem_fechamento(self):
        ts = importacao.ler_arquivo("extrato.ofx", OFX_SGML)
        self.assertEqual([(t.data, t.descricao, t.valor, t.id_externo) for t in ts], [
            (date(2026, 10, 5), "Compra no debito - PADARIA DO ZE", Decimal("-45.90"), "ofx:A1"),
            (date(2026, 10, 6), "PIX RECEBIDO Maria", Decimal("1500.00"), "ofx:A2"),
        ])

    def test_ofx_xml(self):
        [t] = importacao.ler_arquivo("x.ofx", OFX_XML)
        self.assertEqual((t.descricao, t.valor, t.id_externo), ("Uber", Decimal("-12.00"), "ofx:X9"))

    def test_csv_nubank(self):
        csv = "Data,Valor,Identificador,Descrição\n05/10/2026,-45.90,abc-1,Padaria\n06/10/2026,3000.00,abc-2,Salário\n"
        ts = importacao.ler_arquivo("nu.csv", csv.encode())
        self.assertEqual([(t.data, t.valor, t.id_externo) for t in ts],
                         [(date(2026, 10, 5), Decimal("-45.90"), "csv:abc-1"), (date(2026, 10, 6), Decimal("3000.00"), "csv:abc-2")])

    def test_csv_com_ponto_e_virgula_cabecalho_tardio_e_numero_brasileiro(self):
        csv = ("Extrato Conta Corrente\nConta;12345\n\nData Lançamento;Histórico;Descrição;Valor;Saldo\n"
               "05/10/2026;Pix enviado;Joana;-1.234,56;100,00\n06/10/2026;Saldo do dia;;;\n"
               "07/10/2026;Compra;Mercado;-80,00;20,00\n07/10/2026;Compra;Mercado;-80,00;-60,00\n")
        ts = importacao.ler_arquivo("inter.csv", csv.encode("cp1252"))
        self.assertEqual([t.valor for t in ts], [Decimal("-1234.56"), Decimal("-80.00"), Decimal("-80.00")])
        self.assertEqual(ts[0].descricao, "Pix enviado")
        self.assertNotEqual(ts[1].id_externo, ts[2].id_externo)   # duas compras iguais no mesmo dia não se confundem

    def test_erros(self):
        with self.assertRaisesMessage(importacao.ErroImportacao, "colunas"):
            importacao.ler_arquivo("x.csv", b"a,b\n1,2\n")
        with self.assertRaisesMessage(importacao.ErroImportacao, "2 MB"):
            importacao.ler_arquivo("x.csv", b"x" * (importacao.TAMANHO_MAXIMO + 1))
        with self.assertRaisesMessage(importacao.ErroImportacao, "Nenhuma"):
            importacao.ler_arquivo("x.ofx", b"<OFX></OFX>")

    def test_numeros(self):
        for texto, valor in [("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("R$ -12,00", "-12.00"), ("45", "45")]:
            self.assertEqual(importacao._numero(texto), Decimal(valor))


class SugestoesTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 9, 1), gasto_mensal=Decimal("1500"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("500"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("5000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1800"), dia=10)
        self.luz = ContaFixa.objects.create(conta=self.conta, nome="Luz", valor=Decimal("200"), dia=15, variavel=True)
        cartao = Cartao.objects.create(conta=self.conta, nome="Nu", dia_fechamento=1, dia_vencimento=8)
        CompraCartao.objects.create(cartao=cartao, descricao="TV", valor_total=Decimal("900"), parcelas=3,
                                    data_compra=date(2026, 9, 20))
        Lancamento.objects.create(conta=self.conta, data=date(2026, 10, 2), descricao="Velha", valor=10,
                                  id_externo="ofx:JA")
        self.hoje = date(2026, 10, 20)

    def t(self, dia, descricao, valor, id_externo=None, mes=10):
        return importacao.Transacao(date(2026, mes, dia), descricao, Decimal(valor), id_externo or f"ofx:{descricao}")

    def test_cada_tipo_de_sugestao(self):
        ts = [
            self.t(5, "PAGTO SALARIO EMPRESA", "5000.00"),
            self.t(10, "ALUGUEL", "-1800.00"),
            self.t(16, "ENEL ENERGIA", "-243.17"),
            self.t(8, "PAGAMENTO FATURA", "-300.00"),
            self.t(6, "APLICACAO CDB", "-500.00"),
            self.t(2, "Velha", "-10.00", "ofx:JA"),
            self.t(12, "Padaria do Zé", "-23.50"),
            self.t(13, "Notebook loja", "-3200.00"),
            self.t(14, "PIX RECEBIDO", "150.00"),
            self.t(20, "Antes", "-5.00", mes=8),
        ]
        importacao.sugerir(self.conta, ts, self.hoje)
        acoes = {t.descricao: (t.acao, t.motivo) for t in ts}
        self.assertEqual(acoes["PAGTO SALARIO EMPRESA"], ("ignorar", "já previsto: Salário"))
        self.assertEqual(acoes["ALUGUEL"], ("ignorar", "já previsto: Aluguel"))
        self.assertEqual(acoes["ENEL ENERGIA"][0], f"pagar:{self.luz.pk}:2026-10-15")
        self.assertEqual(acoes["PAGAMENTO FATURA"], ("ignorar", "já previsto: Fatura Nu"))
        self.assertEqual(acoes["APLICACAO CDB"][0], "confirmar:2026-10-01")
        self.assertEqual(acoes["Velha"], ("ignorar", "já importada antes"))
        self.assertEqual(acoes["Padaria do Zé"][0], "gasto")
        self.assertEqual(acoes["Notebook loja"][0], "saida")
        self.assertEqual(acoes["PIX RECEBIDO"][0], "entrada")
        self.assertEqual(acoes["Antes"], ("ignorar", "antes do início do cálculo"))
        padaria = next(t for t in ts if t.descricao == "Padaria do Zé")
        self.assertEqual(self.familia.categorias.get(pk=padaria.categoria_id).nome, "Mercado")

    def test_aplicar(self):
        ts = [self.t(16, "ENEL ENERGIA", "-243.17"), self.t(6, "APLICACAO CDB", "-480.00"),
              self.t(12, "Padaria", "-23.50"), self.t(13, "Notebook", "-3200.00"), self.t(14, "PIX", "150.00"),
              self.t(15, "Ignorada", "-1.00")]
        importacao.sugerir(self.conta, ts, self.hoje)
        escolhas = {i: (t.acao, None) for i, t in enumerate(ts)}
        escolhas[5] = ("ignorar", None)
        resumo = importacao.aplicar(self.conta, ts, escolhas, {}, self.hoje)
        self.assertEqual(resumo, {"pagar": 1, "confirmar": 1, "gasto": 1, "saida": 1, "entrada": 1})
        p = PagamentoConta.objects.get(fixa=self.luz)
        self.assertEqual((p.valor, p.data, p.referencia), (Decimal("243.17"), date(2026, 10, 16), date(2026, 10, 15)))
        a = Aporte.objects.get()
        self.assertEqual((a.tipo, a.referencia, a.valor), (Aporte.MENSAL, date(2026, 10, 1), Decimal("480.00")))
        self.assertEqual(GastoDia.objects.get().id_externo, "ofx:Padaria")
        self.assertEqual(set(Lancamento.objects.exclude(descricao="Velha").values_list("descricao", "tipo")),
                         {("Notebook", "saida"), ("PIX", "entrada")})
        # importar de novo (outra página: a conta é carregada de novo): tudo o que entrou é reconhecido
        importacao.sugerir(Conta.objects.get(pk=self.conta.pk), ts, self.hoje)
        self.assertEqual({t.acao for t in ts[:5]}, {"ignorar"})
        self.assertEqual(ts[1].motivo, "já registrado em Investimentos")
        self.assertEqual(ts[5].acao, "gasto")   # a que foi ignorada continua disponível


class TelasImportacaoTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        hoje = date.today()
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=hoje - timedelta(days=30), gasto_mensal=Decimal("1500"))
        self.dia = (hoje - timedelta(days=2)).strftime("%d/%m/%Y")

    def enviar(self, conteudo, nome="extrato.csv"):
        return self.client.post(reverse("financas:importar", args=[self.conta.pk]),
                                {"arquivo": SimpleUploadedFile(nome, conteudo)})

    def test_fluxo_completo_e_reimportar(self):
        csv = f"Data,Valor,Identificador,Descrição\n{self.dia},-30.00,i1,Uber\n{self.dia},250.00,i2,Venda\n".encode()
        r = self.enviar(csv)
        self.assertRedirects(r, reverse("financas:importar_revisar", args=[self.conta.pk]))
        url = reverse("financas:importar_revisar", args=[self.conta.pk])
        r = self.client.get(url)
        self.assertContains(r, "Uber")
        self.assertContains(r, "2 transações")
        # uma ação inválida vira "ignorar"
        r = self.client.post(url, {"acao-0": "gasto", "categoria-0": "", "acao-1": "inventada"})
        self.assertRedirects(r, reverse("financas:painel", args=[self.conta.pk]), fetch_redirect_response=False)
        g = GastoDia.objects.get()
        self.assertEqual((g.descricao, g.valor, g.categoria.nome), ("Uber", Decimal("30.00"), "Transporte"))
        self.assertFalse(Lancamento.objects.exists())
        self.assertEqual(self.client.get(url).status_code, 302)   # sessão limpa
        self.enviar(csv)
        r = self.client.get(url)
        self.assertContains(r, "já importada antes")

    def test_arquivo_invalido_e_cancelar(self):
        r = self.enviar(b"nada,aqui\n1,2\n")
        self.assertContains(r, "Não encontrei as colunas")
        self.enviar(f"Data,Valor,Descrição\n{self.dia},-1,X\n".encode())
        url = reverse("financas:importar_revisar", args=[self.conta.pk])
        self.client.post(url, {"cancelar": "1", "acao-0": "gasto"})
        self.assertFalse(GastoDia.objects.exists())
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_outra_familia(self):
        from .models import Familia
        outra = Conta.objects.create(familia=Familia.objects.create(nome="X"), nome="Deles", saldo_inicial=0,
                                     data_inicio=date.today())
        self.assertEqual(self.client.get(reverse("financas:importar", args=[outra.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("financas:importar_revisar", args=[outra.pk])).status_code, 404)
