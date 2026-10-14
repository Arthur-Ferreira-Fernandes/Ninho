from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import insights
from .calculos import extrato_mes, totais_do_mes
from .categorias import categoria_pelas_regras, normalizar
from .forms import AporteForm
from .models import (Aporte, AssinaturaCartao, Cartao, Categoria, CompraCartao, Conferencia, Conta, ContaFixa,
                     Familia, GastoDia, Lancamento, Objetivo, PagamentoConta, UsoDiario)
from .tests import cadastrar


def familia_de(username="arthur"):
    return User.objects.get(username=username).membro.familia


class RegrasTests(TestCase):
    def test_palavras_chave(self):
        casos = {"Uber *trip": "Transporte", "Supermercado Extra": "Mercado", "Amazon Prime": "Assinaturas",
                 "Amazon": "Compras", "Gasolina": "Transporte", "Gás": "Casa", "Almoço": "Alimentação fora",
                 "Farmácia": "Saúde", "PIX enviado": None, "": None}
        for texto, esperado in casos.items():
            self.assertEqual(categoria_pelas_regras(texto), esperado, texto)

    def test_normalizar(self):
        self.assertEqual(normalizar("  Almoço   no  CENTRO "), "almoco no centro")


class CategoriasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.familia = familia_de()
        self.conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date.today() - timedelta(days=40), gasto_mensal=Decimal("900"))

    def test_padrao_criadas_uma_vez(self):
        nomes = list(self.familia.lista_categorias().values_list("nome", flat=True))
        self.assertIn("Mercado", nomes)
        self.assertEqual(nomes[-1], "Outros")
        Categoria.objects.get(familia=self.familia, nome="Pets").delete()
        self.familia.refresh_from_db()
        self.assertFalse(self.familia.lista_categorias().filter(nome="Pets").exists())  # não recria

    def test_sugestao_aprende_com_o_historico(self):
        mercado = self.familia.lista_categorias().get(nome="Mercado")
        self.assertIsNone(self.familia.sugerir_categoria("Refri"))
        GastoDia.objects.create(conta=self.conta, data=date.today(), descricao="Refri", valor=8, categoria=mercado)
        self.assertEqual(self.familia.sugerir_categoria("refri"), mercado)
        self.assertEqual(self.familia.sugerir_categoria("Uber").nome, "Transporte")

    def test_gasto_do_dia_automatico_e_escolhido(self):
        url = reverse("financas:gasto_dia_adicionar", args=[self.conta.pk])
        hoje = date.today().isoformat()
        self.client.post(url, {"data": hoje, "descricao": "Padaria", "valor": "12", "categoria": ""})
        lazer = self.familia.lista_categorias().get(nome="Lazer")
        self.client.post(url, {"data": hoje, "descricao": "Padaria", "valor": "30", "categoria": lazer.pk})
        self.assertEqual(list(GastoDia.objects.order_by("valor").values_list("categoria__nome", flat=True)),
                         ["Mercado", "Lazer"])
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(r, 'class="cat-chip">Mercado')

    def test_lancamento_de_entrada_nao_tem_categoria(self):
        url = reverse("financas:lancamento_novo", args=[self.conta.pk])
        mercado = self.familia.lista_categorias().get(nome="Mercado")
        hoje = date.today().isoformat()
        self.client.post(url, {"tipo": "entrada", "descricao": "Venda", "valor": "100", "data": hoje,
                               "categoria": mercado.pk})
        self.client.post(url, {"tipo": "saida", "descricao": "Uber", "valor": "30", "data": hoje})
        self.assertEqual(dict(Lancamento.objects.values_list("descricao", "categoria__nome")),
                         {"Venda": None, "Uber": "Transporte"})

    def test_compra_e_conta_fixa(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Nu", dia_fechamento=1, dia_vencimento=10)
        self.client.post(reverse("financas:compra_nova", args=[self.conta.pk]), {
            "cartao": cartao.pk, "descricao": "Shopee", "valor_total": "90", "parcelas": "3",
            "data_compra": date.today().isoformat()})
        self.assertEqual(CompraCartao.objects.get().categoria.nome, "Compras")
        self.client.post(reverse("financas:fixa_nova", args=[self.conta.pk]), {
            "tipo": "despesa", "nome": "Internet", "valor": "120", "frequencia": "mensal", "dia": "10"})
        self.client.post(reverse("financas:fixa_nova", args=[self.conta.pk]), {
            "tipo": "receita", "nome": "Aluguel recebido", "valor": "900", "frequencia": "mensal", "dia": "5"})
        self.assertEqual(dict(ContaFixa.objects.values_list("nome", "categoria__nome")),
                         {"Internet": "Casa", "Aluguel recebido": None})

    def test_pagina_criar_editar_excluir(self):
        url = reverse("financas:categorias")
        self.client.post(url, {"nova-nome": "  Filhos  ", "nova-limite_mensal": "500"})
        filhos = Categoria.objects.get(familia=self.familia, nome="Filhos")
        self.assertEqual(filhos.limite_mensal, Decimal("500"))
        r = self.client.post(url, {"nova-nome": "filhos"}, follow=True)
        self.assertContains(r, "Já existe uma categoria")
        self.client.post(reverse("financas:categoria_editar", args=[filhos.pk]),
                         {f"c{filhos.pk}-nome": "Crianças", f"c{filhos.pk}-limite_mensal": "0"})
        filhos.refresh_from_db()
        self.assertEqual((filhos.nome, filhos.limite_mensal), ("Crianças", None))
        g = GastoDia.objects.create(conta=self.conta, data=date.today(), descricao="Escola", valor=10, categoria=filhos)
        self.assertContains(self.client.get(url), "1 item")
        self.client.post(reverse("financas:categoria_excluir", args=[filhos.pk]))
        g.refresh_from_db()
        self.assertIsNone(g.categoria)

    def test_outra_familia_nao_mexe(self):
        outra = Familia.objects.create(nome="Vizinhos")
        c = Categoria.objects.create(familia=outra, nome="Deles")
        self.assertEqual(self.client.post(reverse("financas:categoria_excluir", args=[c.pk])).status_code, 404)
        self.assertNotIn(c, self.familia.lista_categorias())

    def test_classificar_com_mesma_descricao(self):
        hoje = date.today()
        a = GastoDia.objects.create(conta=self.conta, data=hoje, descricao="Refri", valor=8)
        b = GastoDia.objects.create(conta=self.conta, data=hoje - timedelta(days=1), descricao="refri", valor=9)
        l = Lancamento.objects.create(conta=self.conta, data=hoje, descricao="Refri", valor=20)
        mercado = self.familia.lista_categorias().get(nome="Mercado")
        r = self.client.get(reverse("financas:analise", args=[self.conta.pk]))
        self.assertContains(r, f'name="item-gasto-{a.pk}"')   # um só grupo "refri", representado pelo mais recente
        self.assertContains(r, "3 vezes")
        self.client.post(reverse("financas:classificar", args=[self.conta.pk]),
                         {f"item-gasto-{a.pk}": mercado.pk})
        for item in (a, b, l):
            item.refresh_from_db()
            self.assertEqual(item.categoria, mercado)


class InsightsCalculosTests(TestCase):
    """Janeiro de 2026 completo, com hoje = 15/02/2026."""

    def setUp(self):
        self.familia = Familia.objects.create(nome="Teste")
        cats = {c.nome: c for c in self.familia.lista_categorias()}
        self.cats = {c.pk: c for c in cats.values()}
        self.mercado, self.casa, self.lazer = cats["Mercado"], cats["Casa"], cats["Lazer"]
        self.conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("500"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("5000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10, categoria=self.casa)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 12), descricao="Show", valor=Decimal("200"),
                                  categoria=self.lazer)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 13), descricao="Freela", valor=Decimal("300"),
                                  tipo=Lancamento.ENTRADA)
        cartao = Cartao.objects.create(conta=self.conta, nome="Nu", dia_fechamento=1, dia_vencimento=10)
        CompraCartao.objects.create(cartao=cartao, descricao="Geladeira", valor_total=Decimal("300"), parcelas=3,
                                    data_compra=date(2025, 12, 20), categoria=self.casa)
        AssinaturaCartao.objects.create(cartao=cartao, descricao="Netflix", valor=Decimal("40"), dia_cobranca=5,
                                        inicio=date(2025, 12, 1))
        GastoDia.objects.create(conta=self.conta, data=date(2026, 1, 3), descricao="Feira", valor=Decimal("25"),
                                categoria=self.mercado)
        self.hoje = date(2026, 2, 15)

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_itens_somam_o_gasto_do_mes(self):
        dias = extrato_mes(self.nova(), 2026, 1)
        itens = insights.itens_de_gasto(dias, self.hoje)
        self.assertEqual(sum(i.valor for i in itens), totais_do_mes(dias)["gasto"])
        totais = insights.totais_por_chave(itens)
        self.assertEqual(totais[self.casa.pk], Decimal("1600"))     # aluguel + parcela da geladeira
        self.assertEqual(totais[self.lazer.pk], Decimal("200"))
        self.assertEqual(totais[None], Decimal("40"))                 # Netflix sem categoria
        self.assertEqual(totais[self.mercado.pk], Decimal("25"))      # gasto detalhado do dia 3
        # o envelope do mês é 310: os 15 a mais do dia 3 saem dos outros 30 dias
        self.assertEqual(totais[insights.DIA_A_DIA], Decimal("285"))

    def test_categorias_com_limite_e_media(self):
        self.lazer.limite_mensal = Decimal("160")
        self.lazer.save()
        conta = self.nova()
        cats = {c.pk: c for c in self.familia.lista_categorias()}
        r = insights.categorias_do_mes(conta, date(2026, 1, 1), self.hoje, cats, {self.lazer.pk: Decimal("100")})
        lazer = next(l for l in r["linhas"] if l["chave"] == self.lazer.pk)
        self.assertEqual((lazer["pct_limite"], lazer["variacao"], lazer["variacao_pct"]), (125, Decimal("100"), 100))
        self.assertEqual(r["linhas"][-1]["chave"], insights.DIA_A_DIA)   # dia a dia sempre por último
        subiram, _ = insights.variacoes(r["linhas"])
        self.assertEqual([l["nome"] for l in subiram], ["Lazer"])

    def test_fechamento_sugere_guardar_a_sobra(self):
        f = insights.fechamento(self.nova(), date(2026, 1, 1), self.hoje, self.cats)
        # recebido 5300; gasto 1500+200+100+40+310 (envelope do dia a dia) = 2150; guardado 500 (previsto)
        self.assertEqual((f["totais"]["recebido"], f["totais"]["gasto"], f["sobra"]),
                         (Decimal("5300"), Decimal("2150"), Decimal("2650")))
        self.assertTrue(f["encerrado"])
        self.assertIn("sobra", [s["tipo"] for s in f["sugestoes"]])
        self.assertEqual(f["dia_a_dia"]["usado"], Decimal("310.00"))

    def test_poupanca_e_sequencia(self):
        hoje = date(2026, 4, 10)
        for mes, valor in [(1, "500"), (2, "0"), (3, "450")]:
            Aporte.objects.create(conta=self.conta, tipo=Aporte.MENSAL, referencia=date(2026, mes, 1),
                                  data=date(2026, mes, 6), valor=Decimal(valor))
        p = insights.historico_poupanca(self.nova(), hoje)
        self.assertEqual([l["situacao"] for l in p["linhas"]], ["confirmado", "nao_guardado", "confirmado", "previsto"])
        self.assertEqual(p["sequencia"], 1)          # março; fevereiro (não guardou) interrompe; abril ainda aberto
        jan = p["linhas"][0]
        self.assertEqual(jan["taxa"], round(Decimal("500") * 100 / jan["recebido"], 1))
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 4, 2), valor=Decimal("50"))
        self.assertEqual(insights.historico_poupanca(self.nova(), hoje)["sequencia"], 2)   # abril com avulso conta
        self.assertEqual(insights.meses_sem_confirmar(self.nova(), hoje), [])
        self.assertEqual(insights.meses_sem_confirmar(self.nova(), date(2026, 6, 10)), [date(2026, 5, 1), date(2026, 4, 1)])

    def test_ritmo_do_dia_a_dia(self):
        hoje = date(2026, 1, 10)
        for d in range(1, 11):
            GastoDia.objects.create(conta=self.conta, data=date(2026, 1, d), descricao="x", valor=Decimal("20"))
        r = insights.ritmo_dia_a_dia(self.nova(), hoje)
        # 10 dias a 20 + a feira de 25 no dia 3: 22,50/dia; os 310 do mês duram 14 dias
        self.assertEqual((r["por_dia"], r["acaba_em"]), (Decimal("22.50"), date(2026, 1, 14)))
        UsoDiario.objects.all().delete()
        self.assertIsNone(insights.ritmo_dia_a_dia(Conta.objects.create(
            familia=self.familia, nome="Outra", saldo_inicial=0, data_inicio=date(2026, 1, 1)), hoje))

    def test_conta_variavel_acima_da_media(self):
        luz = ContaFixa.objects.create(conta=self.conta, nome="Luz", valor=Decimal("150"), dia=15, variavel=True)
        for mes, valor in [(1, "140"), (2, "150"), (3, "145"), (4, "200")]:
            PagamentoConta.objects.create(fixa=luz, referencia=date(2026, mes, 15), data=date(2026, mes, 15),
                                          valor=Decimal(valor))
        [v] = insights.contas_variaveis_acima(self.nova())
        self.assertEqual((v["fixa"], v["media"], v["pct"]), (luz, Decimal("145.00"), 38))

    def test_vazamento(self):
        conta = self.nova()
        esperado = insights.saldo_esperado(conta, date(2026, 1, 31)).esperado
        Conferencia.objects.create(conta=conta, data=date(2026, 1, 31), saldo_real=esperado - Decimal("400"))
        v = insights.vazamento(self.nova(), self.hoje)
        self.assertEqual(v["diferenca"], Decimal("-400"))
        self.assertTrue(v["relevante"])
        self.assertEqual(v["dias_sem_conferir"], 15)

    def test_compromisso_do_cartao(self):
        c = insights.compromisso_cartao(self.nova(), date(2026, 1, 15))
        self.assertEqual(c["restante"], Decimal("200"))    # parcelas de fevereiro e março
        self.assertEqual((c["proximo"]["valor"], c["proximo"]["pct"]), (Decimal("100"), 2))

    def test_liberacoes(self):
        conta = self.nova()
        lib = insights.liberacoes(conta, date(2026, 4, 5))
        self.assertEqual([(i["nome"], i["mes"], i["valor"]) for i in lib["ja"]],
                         [("Geladeira", date(2026, 4, 1), Decimal("100"))])   # última parcela em março
        conta.liberado_visto_ate = date(2026, 4, 1)
        self.assertEqual(insights.liberacoes(conta, date(2026, 4, 5))["ja"], [])

    def test_assinaturas_anuais(self):
        a = insights.assinaturas_anuais(self.nova(), date(2026, 7, 1))
        self.assertEqual((a["anual"], len(a["a_revisar"])), (Decimal("480"), 1))   # desde 12/2025: mais de 180 dias

    def test_projecao_do_objetivo(self):
        o = Objetivo.objects.create(conta=self.conta, nome="Reserva", valor_alvo=Decimal("6000"),
                                    valor_inicial=Decimal("1000"), padrao=True, prazo=date(2026, 12, 31))
        p = insights.projecao_objetivo(o, self.nova(), date(2026, 2, 15))
        # faltam 5000 a 500/mês: 10 meses depois de fevereiro
        self.assertEqual((p["falta"], p["ritmo"], p["chega_em"]), (Decimal("5000"), Decimal("500"), date(2026, 12, 1)))
        self.assertEqual((p["por_mes_prazo"], p["no_prazo"]), (Decimal("500.00"), True))


class AnaliseTelasTests(TestCase):
    """Telas e ações, com datas relativas a hoje (as views usam a data real)."""

    def setUp(self):
        cadastrar(self.client)
        self.familia = familia_de()
        hoje = date.today()
        self.inicio = insights.somar_meses(hoje.replace(day=1), -3)
        self.conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=self.inicio, gasto_mensal=Decimal("600"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("300"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        self.url = reverse("financas:analise", args=[self.conta.pk])

    def test_pagina_e_painel(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fechamento de")
        self.assertContains(r, "Quanto você guarda")
        painel = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(painel, reverse("financas:analise", args=[self.conta.pk]))

    def test_conta_recem_criada(self):
        nova = Conta.objects.create(familia=self.familia, nome="Nova", saldo_inicial=0, data_inicio=date.today())
        r = self.client.get(reverse("financas:analise", args=[nova.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ainda não há meses completos")

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="X"), nome="Deles", saldo_inicial=0,
                                     data_inicio=date.today())
        self.assertEqual(self.client.get(reverse("financas:analise", args=[outra.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:guardar_sobra", args=[outra.pk])).status_code, 404)

    def test_guardar_a_sobra_uma_vez(self):
        mes = insights.somar_meses(date.today().replace(day=1), -1)
        objetivo = Objetivo.objects.create(conta=self.conta, nome="Viagem", valor_alvo=10000)
        url = reverse("financas:guardar_sobra", args=[self.conta.pk])
        self.client.post(url, {"mes": mes.isoformat(), "objetivo": objetivo.pk})
        a = Aporte.objects.get()
        sobra = insights.fechamento(Conta.objects.get(pk=self.conta.pk), mes, date.today(), {})["sobra"]
        self.assertEqual((a.tipo, a.valor, a.sobra_de, a.objetivo), (Aporte.AVULSO, sobra, mes, objetivo))
        self.assertGreater(a.valor, 0)
        self.client.post(url, {"mes": mes.isoformat()})          # de novo: não duplica
        self.assertEqual(Aporte.objects.count(), 1)
        self.client.post(url, {"mes": date.today().replace(day=1).isoformat()})   # mês aberto: não
        self.assertEqual(Aporte.objects.count(), 1)

    def test_somar_dinheiro_liberado(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Nu", dia_fechamento=1, dia_vencimento=10)
        # comprada no dia 1 (já fechada): faturas de 2 e de 1 mês atrás; a última parcela venceu no mês passado
        CompraCartao.objects.create(cartao=cartao, descricao="TV", valor_total=Decimal("400"), parcelas=2,
                                    data_compra=insights.somar_meses(date.today().replace(day=1), -3))
        self.assertEqual(insights.liberacoes(self.conta, date.today())["total_ja"], Decimal("200"))
        self.client.post(reverse("financas:liberado_decidir", args=[self.conta.pk]), {"acao": "somar"})
        conta = Conta.objects.get(pk=self.conta.pk)
        self.assertEqual((conta.guardar_valor, conta.liberado_visto_ate), (Decimal("500"), date.today().replace(day=1)))
        self.assertEqual(insights.liberacoes(conta, date.today())["ja"], [])

    def test_revisar_assinatura(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Nu", dia_fechamento=1, dia_vencimento=10)
        a = AssinaturaCartao.objects.create(cartao=cartao, descricao="Spotify", valor=Decimal("22"), dia_cobranca=5,
                                            inicio=date.today() - timedelta(days=400))
        self.assertContains(self.client.get(self.url), "Ainda uso")
        self.client.post(reverse("financas:assinatura_revisar", args=[a.pk]))
        a.refresh_from_db()
        self.assertEqual(a.revisada_em, date.today())
        self.assertNotContains(self.client.get(self.url), "Ainda uso")


class ObjetivosTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.conta = Conta.objects.create(familia=familia_de(), nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date.today() - timedelta(days=60),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("300"))

    def test_criar_editar_e_um_so_padrao(self):
        url = reverse("financas:objetivo_novo", args=[self.conta.pk])
        self.assertTrue(self.client.get(url).context["form"]["padrao"].value())   # o primeiro já vem como padrão
        self.client.post(url, {"nome": "Reserva", "valor_alvo": "10000", "valor_inicial": "2000", "padrao": "on"})
        self.client.post(url, {"nome": "Viagem", "valor_alvo": "5000", "valor_inicial": "", "padrao": "on"})
        self.assertEqual(dict(Objetivo.objects.values_list("nome", "padrao")), {"Reserva": False, "Viagem": True})
        r = self.client.post(url, {"nome": "X", "valor_alvo": "0"})
        self.assertContains(r, "maior que zero")
        reserva = Objetivo.objects.get(nome="Reserva")
        self.client.post(reverse("financas:objetivo_editar", args=[reserva.pk]),
                         {"nome": "Reserva de emergência", "valor_alvo": "12000", "valor_inicial": "2000"})
        reserva.refresh_from_db()
        self.assertEqual((reserva.nome, reserva.valor_alvo), ("Reserva de emergência", Decimal("12000")))
        painel = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(painel, "Reserva de emergência")

    def test_guardado_vai_para_o_objetivo(self):
        reserva = Objetivo.objects.create(conta=self.conta, nome="Reserva", valor_alvo=10000, padrao=True)
        viagem = Objetivo.objects.create(conta=self.conta, nome="Viagem", valor_alvo=3000)
        hoje = date.today()
        avulso = reverse("financas:aporte_avulso", args=[self.conta.pk])
        self.client.post(avulso, {"valor": "100", "data": hoje.isoformat(), "objetivo": viagem.pk})
        self.client.post(avulso, {"valor": "50", "data": hoje.isoformat(), "objetivo": ""})   # "Sem objetivo"
        form = AporteForm({"valor": "70", "data": hoje.isoformat()}, conta=self.conta, hoje=hoje,
                          instance=Aporte(conta=self.conta, tipo=Aporte.AVULSO))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()                                                    # sem o campo: vai para o padrão
        self.assertEqual(sorted(Aporte.objects.values_list("valor", "objetivo__nome")),
                         [(Decimal("50"), None), (Decimal("70"), "Reserva"), (Decimal("100"), "Viagem")])
        self.assertEqual((reserva.guardado, viagem.guardado), (Decimal("70"), Decimal("100")))
        viagem.delete()
        self.assertEqual(Aporte.objects.count(), 3)   # excluir o objetivo não apaga o guardado
