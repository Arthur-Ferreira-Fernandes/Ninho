from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .calculos import Dia, totais_do_mes, aplicar_escala, nivel_cor, dia_de_guardar, guardados, eventos_fixa, proximas_fixas, cobrancas, cronograma, proximo_fechamento, serie_diaria, datas_da_fatura, faturas, meses_do_seletor, primeira_fatura, extrato_mes, gasto_no_periodo, saldo_esperado, simular_mes
from .models import (Aporte, AssinaturaCartao, GastoDia, Cartao, CompraCartao, PagamentoConta, Conferencia, Conta, ContaFixa, Familia, Lancamento, Membro,
                     PedidoEntrada, UsoDiario)


class SaldoEsperadoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(
            familia=Familia.objects.create(nome="Teste"), nome="Nubank", saldo_inicial=Decimal("1000"),
            data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"),  # 10/dia em janeiro
        )

    def test_so_gasto_diario(self):
        # 1 a 10 de janeiro = 10 dias
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 10)).esperado, Decimal("900"))

    def test_fixas_e_avulsos(self):
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("500"), dia=5)
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("3000"), dia=5,
                                 tipo=ContaFixa.RECEITA)
        ContaFixa.objects.create(conta=self.conta, nome="Inativa", valor=Decimal("99"), dia=2, ativa=False)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 3), descricao="Freela",
                                  valor=Decimal("200"), tipo=Lancamento.ENTRADA)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 20), descricao="Depois",
                                  valor=Decimal("50"))
        r = saldo_esperado(self.conta, date(2026, 1, 10))
        self.assertEqual(r.esperado, Decimal("1000") + 3000 - 500 - 100 + 200)

    def test_dia_31_em_fevereiro(self):
        ContaFixa.objects.create(conta=self.conta, nome="Cartão", valor=Decimal("100"), dia=31)
        r = saldo_esperado(self.conta, date(2026, 2, 28))
        self.assertEqual(r.despesas_fixas, Decimal("200"))  # 31/01 e 28/02

    def test_valor_diario_muda_com_o_mes(self):
        # 31/01 vale 310/31 = 10; fevereiro inteiro soma os 310 do mês (310/28 por dia)
        self.conta.data_inicio = date(2026, 1, 31)
        r = saldo_esperado(self.conta, date(2026, 2, 28))
        self.assertEqual(r.gasto_diario_total, Decimal("320.00"))  # 10 + 310 (fevereiro inteiro)

    def test_modo_percentual(self):
        # salário em dois pagamentos, depois do período calculado
        for dia, valor in [(15, "2000"), (30, "3000")]:
            ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal(valor), dia=dia,
                                     tipo=ContaFixa.RECEITA, salario=True)
        self.conta.modo_gasto = Conta.PERCENTUAL
        self.conta.percentual_gasto = Decimal("31")  # 31% de 5000 = 1550/mês → 50/dia em janeiro
        self.assertEqual(self.conta.salario, Decimal("5000"))
        self.assertEqual(self.conta.orcamento_mensal, Decimal("1550"))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 2)).esperado, Decimal("900"))

    def test_antes_do_inicio(self):
        self.assertEqual(saldo_esperado(self.conta, date(2025, 12, 1)).esperado, Decimal("1000"))


CADASTRO = {"first_name": "Arthur", "email": "", "username": "arthur",
            "password1": "senha-forte-123", "password2": "senha-forte-123"}


def cadastrar(client, username="arthur", senha="senha-forte-123", familia="Família Teste"):
    dados = dict(CADASTRO, username=username, first_name=username.title(),
                 password1=senha, password2=senha)
    client.post(reverse("financas:cadastro"), dados)
    if familia:
        client.post(reverse("financas:sem_familia"), {"criar": "", "criar-nome": familia})


class TelasTests(TestCase):
    def test_fluxo(self):
        self.assertRedirects(self.client.get("/"), reverse("financas:entrar") + "?next=/")
        r = self.client.post(reverse("financas:cadastro"), CADASTRO)
        self.assertRedirects(r, reverse("financas:sem_familia"))
        # sem família, qualquer tela leva para criar/entrar
        self.assertRedirects(self.client.get("/"), reverse("financas:sem_familia"))
        self.client.post(reverse("financas:sem_familia"), {"criar": "", "criar-nome": "Casa"})
        self.assertRedirects(self.client.get("/"), reverse("financas:conta_nova"))
        r = self.client.post(reverse("financas:conta_nova"), {
            "nome": "Itaú", "saldo_inicial": "500", "data_inicio": "2026-01-01",
            "modo_gasto": "mensal", "gasto_mensal": "620", **pagamentos()})  # 20/dia em janeiro
        conta = Conta.objects.get()
        self.assertRedirects(r, reverse("financas:painel", args=[conta.pk]))
        self.client.post(reverse("financas:conferencia_nova", args=[conta.pk]),
                         {"data": "2026-01-05", "saldo_real": "380"})
        r = self.client.get(reverse("financas:painel", args=[conta.pk]) + "?data=2026-01-05")
        self.assertContains(r, "R$ 400,00")   # esperado: 500 - 5×20
        self.assertContains(r, "-R$ 20,00")   # diferença

        conf = Conferencia.objects.get()
        self.client.post(reverse("financas:recomecar", args=[conf.pk]))
        conta.refresh_from_db()
        self.assertEqual((conta.saldo_inicial, conta.data_inicio), (Decimal("380"), date(2026, 1, 6)))


class FamiliaTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.dono = User.objects.get(username="arthur")
        self.familia = self.dono.membro.familia

    def pedir_entrada(self, codigo, username="maria"):
        """Maria se cadastra (com a própria senha) e pede para entrar."""
        outro = self.client_class()
        cadastrar(outro, username=username, senha="outra-senha-456", familia=None)
        outro.post(reverse("financas:sem_familia"),
                   {"entrar": "", "entrar-codigo": codigo, "entrar-parentesco": "esposa"})
        return outro

    def test_cadastro_cria_familia_com_admin_e_codigo(self):
        self.assertEqual(self.familia.nome, "Família Teste")
        self.assertTrue(self.dono.membro.is_admin)
        self.assertEqual(len(self.familia.codigo_convite), 8)

    def test_pedido_aceito(self):
        # código digitado em minúsculas e com espaços também vale
        codigo = self.familia.codigo_convite.lower()
        maria = self.pedir_entrada(f" {codigo[:4]} {codigo[4:]} ")
        pedido = PedidoEntrada.objects.get(usuario__username="maria")
        self.assertEqual(pedido.familia, self.familia)

        # enquanto não é aceita, Maria não acessa nada
        self.assertRedirects(maria.get("/"), reverse("financas:sem_familia"))
        self.assertContains(maria.get(reverse("financas:sem_familia")), "Aguardando aprovação")

        self.client.post(reverse("financas:pedido_aceitar", args=[pedido.pk]))
        membro = Membro.objects.get(usuario__username="maria")
        self.assertEqual((membro.familia, membro.papel, membro.parentesco),
                         (self.familia, Membro.MEMBRO, "esposa"))
        self.assertFalse(PedidoEntrada.objects.exists())

        conta = Conta.objects.create(familia=self.familia, nome="Conjunta", saldo_inicial=0,
                                     data_inicio=date(2026, 1, 1))
        self.assertEqual(maria.get(reverse("financas:painel", args=[conta.pk])).status_code, 200)
        # membro comum não aprova pedidos nem troca o código
        self.assertEqual(maria.post(reverse("financas:codigo_novo")).status_code, 403)

    def test_pedido_recusado(self):
        self.pedir_entrada(self.familia.codigo_convite)
        pedido = PedidoEntrada.objects.get()
        self.client.post(reverse("financas:pedido_recusar", args=[pedido.pk]))
        self.assertFalse(PedidoEntrada.objects.exists())
        self.assertFalse(Membro.objects.filter(usuario__username="maria").exists())

    def test_codigo_invalido(self):
        self.pedir_entrada("XXXXXXXX")
        self.assertFalse(PedidoEntrada.objects.exists())

    def test_novo_codigo_invalida_o_antigo(self):
        antigo = self.familia.codigo_convite
        self.client.post(reverse("financas:codigo_novo"))
        self.familia.refresh_from_db()
        self.assertNotEqual(self.familia.codigo_convite, antigo)
        self.pedir_entrada(antigo)
        self.assertFalse(PedidoEntrada.objects.exists())

    def test_admin_nao_aceita_pedido_de_outra_familia(self):
        outra = Familia.objects.create(nome="Vizinhos")
        intruso = User.objects.create_user("joao", password="x")
        pedido = PedidoEntrada.objects.create(familia=outra, usuario=intruso)
        r = self.client.post(reverse("financas:pedido_aceitar", args=[pedido.pk]))
        self.assertEqual(r.status_code, 404)

    def test_remover_pessoa(self):
        outro = User.objects.create_user("joao", password="x")
        m = Membro.objects.create(familia=self.familia, usuario=outro)
        self.client.post(reverse("financas:membro_remover", args=[m.pk]))
        self.assertFalse(Membro.objects.filter(pk=m.pk).exists())
        # não remove a si mesmo
        self.client.post(reverse("financas:membro_remover", args=[self.dono.membro.pk]))
        self.assertTrue(Membro.objects.filter(usuario=self.dono).exists())

    def test_nao_ve_conta_de_outra_familia(self):
        outra = Familia.objects.create(nome="Vizinhos")
        conta = Conta.objects.create(familia=outra, nome="Deles", saldo_inicial=0,
                                     data_inicio=date(2026, 1, 1))
        self.assertEqual(self.client.get(reverse("financas:painel", args=[conta.pk])).status_code, 404)


def pagamentos(*itens, existentes=0):
    """Dados POST do formset de pagamentos do salário: pagamentos((dia, valor), ...)."""
    dados = {"salario-TOTAL_FORMS": str(len(itens)), "salario-INITIAL_FORMS": str(existentes),
             "salario-MIN_NUM_FORMS": "0", "salario-MAX_NUM_FORMS": "1000"}
    for i, item in enumerate(itens):
        dia, valor = item[:2]
        dados[f"salario-{i}-dia"] = dia
        dados[f"salario-{i}-valor"] = valor
        if len(item) > 2:
            dados[f"salario-{i}-id"] = item[2]
        if len(item) > 3 and item[3]:
            dados[f"salario-{i}-DELETE"] = "on"
    return dados


class ContaFormTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.url = reverse("financas:conta_nova")
        self.base = {"nome": "Nubank", "saldo_inicial": "1000", "data_inicio": "2026-01-01"}

    def post(self, url=None, **campos):
        return self.client.post(url or self.url, {**self.base, **campos})

    def test_primeira_conta_no_layout_de_boas_vindas(self):
        r = self.client.get(self.url)
        self.assertTemplateUsed(r, "financas/auth_base.html")
        self.assertContains(r, "3. Primeira conta")

    def test_salario_em_varios_dias(self):
        self.post(modo_gasto="percentual", percentual_gasto="30",
                  **pagamentos(("15", "1800"), ("30", "2700")))
        conta = Conta.objects.get()
        self.assertEqual(conta.salario, Decimal("4500"))
        self.assertEqual(conta.orcamento_mensal, Decimal("1350"))
        fixas = list(conta.fixas.order_by("dia").values_list("nome", "dia", "valor", "tipo", "salario"))
        self.assertEqual(fixas, [
            ("Salário (dia 15)", 15, Decimal("1800"), ContaFixa.RECEITA, True),
            ("Salário (dia 30)", 30, Decimal("2700"), ContaFixa.RECEITA, True),
        ])
        # segunda conta já usa o layout normal
        self.assertTemplateUsed(self.client.get(self.url), "financas/base.html")

    def test_linha_em_branco_e_ignorada(self):
        self.post(modo_gasto="percentual", percentual_gasto="30", **pagamentos(("5", "4000"), ("", "")))
        conta = Conta.objects.get()
        self.assertEqual(conta.fixas.get().nome, "Salário")

    def test_percentual_exige_pagamento(self):
        r = self.post(modo_gasto="percentual", percentual_gasto="30", **pagamentos(("", "")))
        self.assertContains(r, "Informe pelo menos um pagamento")
        self.assertFalse(Conta.objects.exists())

    def test_pagamento_invalido(self):
        r = self.post(modo_gasto="mensal", gasto_mensal="1000", **pagamentos(("40", "100"), ("5", "0")))
        self.assertContains(r, "Dia entre 1 e 31")
        self.assertContains(r, "Valor maior que zero")
        self.assertFalse(Conta.objects.exists())

    def test_percentual_fora_do_intervalo(self):
        r = self.post(modo_gasto="percentual", percentual_gasto="150", **pagamentos(("5", "4000")))
        self.assertContains(r, "entre 0 e 100")

    def test_mensal_exige_valor(self):
        r = self.post(modo_gasto="mensal", **pagamentos())
        self.assertContains(r, "Informe quanto pode gastar por mês")

    def test_mensal_sem_salario(self):
        self.post(modo_gasto="mensal", gasto_mensal="1500", **pagamentos())
        conta = Conta.objects.get()
        self.assertEqual(conta.orcamento_mensal, Decimal("1500"))
        self.assertFalse(conta.fixas.exists())

    def test_editar_pagamentos(self):
        self.post(modo_gasto="percentual", percentual_gasto="30",
                  **pagamentos(("15", "1800"), ("30", "2700")))
        conta = Conta.objects.get()
        p15, p30 = conta.fixas.order_by("dia")
        aluguel = ContaFixa.objects.create(conta=conta, nome="Aluguel", valor=1000, dia=10)
        # muda o dia 30 para 28, remove o dia 15 e adiciona o dia 5
        self.post(reverse("financas:conta_editar", args=[conta.pk]), modo_gasto="percentual",
                  percentual_gasto="30",
                  **pagamentos(("15", "1800", p15.pk, True), ("28", "2700", p30.pk), ("5", "500"),
                               existentes=2))
        conta = Conta.objects.get()
        self.assertEqual(list(conta.fixas.filter(salario=True).order_by("dia").values_list("dia", "valor")),
                         [(5, Decimal("500")), (28, Decimal("2700"))])
        self.assertEqual(conta.salario, Decimal("3200"))
        self.assertTrue(ContaFixa.objects.filter(pk=aluguel.pk).exists())  # outras fixas intactas

    def test_tela_de_edicao_nao_lista_outras_fixas(self):
        self.post(modo_gasto="mensal", gasto_mensal="1500", **pagamentos(("5", "4000")))
        conta = Conta.objects.get()
        ContaFixa.objects.create(conta=conta, nome="Aluguel", valor=1000, dia=10)
        r = self.client.get(reverse("financas:conta_editar", args=[conta.pk]))
        self.assertEqual(r.context["pagamentos"].total_form_count(), 1)


class ContaFixaFormTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=0,
                                          data_inicio=date(2026, 1, 1), gasto_mensal=1000)
        self.url = reverse("financas:fixa_nova", args=[self.conta.pk])

    def test_tela_nova(self):
        r = self.client.get(self.url)
        self.assertTemplateUsed(r, "financas/conta_fixa_form.html")
        self.assertNotIn("ativa", r.context["form"].fields)

    def test_criar_despesa(self):
        r = self.client.post(self.url, {"tipo": "despesa", "nome": "Aluguel", "valor": "1500", "dia": "10"})
        self.assertRedirects(r, reverse("financas:painel", args=[self.conta.pk]) + "#contas",
                             fetch_redirect_response=False)
        fixa = self.conta.fixas.get()
        self.assertEqual((fixa.nome, fixa.valor, fixa.dia, fixa.tipo, fixa.ativa, fixa.salario),
                         ("Aluguel", Decimal("1500"), 10, "despesa", True, False))

    def test_validacoes(self):
        r = self.client.post(self.url, {"tipo": "despesa", "nome": "X", "valor": "0", "dia": "32"})
        self.assertContains(r, "Informe um valor maior que zero")
        self.assertContains(r, "Escolha um dia entre 1 e 31")
        self.assertFalse(self.conta.fixas.exists())

    def test_salario_nao_vira_despesa(self):
        fixa = ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=4000, dia=5,
                                        tipo=ContaFixa.RECEITA, salario=True)
        url = reverse("financas:item_editar", args=["fixa", fixa.pk])
        self.assertContains(self.client.get(url), "pagamento do <strong>salário</strong>")
        self.client.post(url, {"tipo": "despesa", "nome": "Salário", "valor": "4200", "dia": "6", "ativa": "on"})
        fixa.refresh_from_db()
        self.assertEqual((fixa.tipo, fixa.valor, fixa.dia), (ContaFixa.RECEITA, Decimal("4200"), 6))

    def test_desativar(self):
        fixa = ContaFixa.objects.create(conta=self.conta, nome="Academia", valor=100, dia=5)
        self.client.post(reverse("financas:item_editar", args=["fixa", fixa.pk]),
                         {"tipo": "despesa", "nome": "Academia", "valor": "100", "dia": "5"})
        fixa.refresh_from_db()
        self.assertFalse(fixa.ativa)


class ExtratoTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # 1000/mês gera centavos quebrados (1000/31 = 32,258...)
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 10), gasto_mensal=Decimal("1000"))
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=15)
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 2, 3), descricao="Freela",
                                  valor=Decimal("300"), tipo=Lancamento.ENTRADA)
        Conferencia.objects.create(conta=self.conta, data=date(2026, 2, 3), saldo_real=Decimal("6000"))

    def test_bate_com_saldo_esperado_todos_os_dias(self):
        for ano, mes in [(2026, 1), (2026, 2), (2026, 3)]:
            for dia in extrato_mes(self.conta, ano, mes):
                if dia.ativo:
                    self.assertEqual(dia.esperado, saldo_esperado(self.conta, dia.data).esperado, dia.data)
                    self.assertEqual(dia.inicio + dia.entradas - dia.saidas - dia.gasto, dia.esperado)

    def test_dias_antes_do_inicio_ficam_inativos(self):
        dias = extrato_mes(self.conta, 2026, 1)
        self.assertEqual(len(dias), 31)
        self.assertFalse(dias[8].ativo)       # 09/01
        self.assertTrue(dias[9].ativo)        # 10/01
        self.assertEqual(dias[9].inicio, Decimal("5000"))

    def test_movimentos_e_conferencia_no_dia(self):
        dias = extrato_mes(self.conta, 2026, 2)
        dia3 = dias[2]
        self.assertEqual([m.nome for m in dia3.movimentos], ["Freela"])
        self.assertEqual(dia3.entradas, Decimal("300"))
        self.assertEqual(dia3.diferenca, Decimal("6000") - dia3.esperado)
        self.assertEqual([m.nome for m in dias[4].movimentos], ["Salário"])
        self.assertEqual(dias[14].saidas, Decimal("1500"))

    def test_tela_com_dia_selecionado(self):
        url = reverse("financas:painel", args=[self.conta.pk])
        r = self.client.get(url + "?mes=2026-02&dia=2026-02-03")
        self.assertEqual(r.context["dia"].data, date(2026, 2, 3))
        self.assertEqual(len(r.context["dias"]), 28)
        self.assertContains(r, "Freela")
        self.assertContains(r, "Saldo conferido")
        # mês passado sem dia escolhido abre no fechamento; parâmetros inválidos não quebram
        self.assertEqual(self.client.get(url + "?mes=2026-03").context["dia"].data, date(2026, 3, 31))
        self.assertEqual(self.client.get(url + "?mes=xx&dia=yy").status_code, 200)

    def test_lancamento_com_data_do_dia(self):
        r = self.client.get(reverse("financas:lancamento_novo", args=[self.conta.pk]) + "?data=2026-02-20")
        self.assertEqual(r.context["form"].initial["data"], date(2026, 2, 20))


class UsoDiarioTests(TestCase):
    """Dinheiro do dia a dia como envelope: o que não é usado é diluído no resto do mês."""

    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # novembro tem 30 dias: 300/mês = 10/dia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 11, 1), gasto_mensal=Decimal("300"))

    def usar(self, dia, situacao, valor=None):
        UsoDiario.objects.create(conta=self.conta, data=date(2026, 11, dia), situacao=situacao, valor=valor)

    def mes(self):
        return simular_mes(self.conta, 2026, 11)

    def test_sem_registro_e_igual_ao_valor_fixo(self):
        dias = self.mes()
        self.assertTrue(all(d.disponivel == Decimal("10") and d.gasto == Decimal("10") for d in dias))

    def test_nao_usei_dilui_nos_dias_seguintes(self):
        self.usar(1, UsoDiario.NADA)
        dias = self.mes()
        self.assertEqual(dias[0].gasto, 0)
        self.assertEqual(dias[1].disponivel, Decimal("10.34"))   # 300 / 29
        self.assertEqual(sum(d.gasto for d in dias), Decimal("300"))  # o mês fecha no orçamento
        # no fim do dia 1 a conta tem 10 a mais do que antes
        self.assertEqual(saldo_esperado(self.conta, date(2026, 11, 1)).esperado, Decimal("1000"))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 11, 30)).esperado, Decimal("700"))

    def test_usei_parte_e_usei_mais(self):
        self.usar(1, UsoDiario.PARTE, Decimal("4"))    # sobra 6
        self.usar(2, UsoDiario.PARTE, Decimal("40"))   # passa 30 do disponível
        dias = self.mes()
        self.assertEqual(dias[1].disponivel, Decimal("10.21"))   # (300-4)/29
        self.assertEqual(dias[2].disponivel, Decimal("9.14"))    # (300-44)/28 = 9,1428...
        self.assertEqual(sum(d.gasto for d in dias), Decimal("300"))

    def test_disponivel_nunca_fica_negativo(self):
        self.usar(1, UsoDiario.PARTE, Decimal("500"))
        dias = self.mes()
        self.assertEqual(dias[1].disponivel, 0)
        self.assertEqual(sum(d.gasto for d in dias), Decimal("500"))

    def test_sobra_do_ultimo_dia_fica_na_conta(self):
        self.usar(30, UsoDiario.NADA)
        self.assertEqual(gasto_no_periodo(self.conta, date(2026, 11, 1), date(2026, 11, 30)), Decimal("290"))
        # dezembro começa do zero, com o orçamento normal
        self.assertEqual(simular_mes(self.conta, 2026, 12)[0].disponivel, Decimal("9.68"))  # 300/31

    def test_extrato_bate_com_saldo_esperado(self):
        self.usar(3, UsoDiario.NADA)
        self.usar(7, UsoDiario.PARTE, Decimal("25.37"))
        self.usar(8, UsoDiario.TUDO)
        for dia in extrato_mes(self.conta, 2026, 11) + extrato_mes(self.conta, 2026, 12):
            self.assertEqual(dia.esperado, saldo_esperado(self.conta, dia.data).esperado, dia.data)

    def post(self, **dados):
        return self.client.post(reverse("financas:uso_diario", args=[self.conta.pk]), dados)

    def test_tela_registra_altera_e_desfaz(self):
        hoje = date.today()
        self.conta.data_inicio = hoje.replace(day=1)
        self.conta.save()
        r = self.post(data=hoje.isoformat(), situacao="nada")
        self.assertRedirects(r, reverse("financas:painel", args=[self.conta.pk])
                             + f"?mes={hoje:%Y-%m}&dia={hoje:%Y-%m-%d}#extrato", fetch_redirect_response=False)
        self.assertEqual(self.conta.usos.get().situacao, UsoDiario.NADA)
        self.post(data=hoje.isoformat(), situacao="parte", valor="12.50")
        uso = self.conta.usos.get()
        self.assertEqual((uso.situacao, uso.valor), (UsoDiario.PARTE, Decimal("12.50")))
        self.post(data=hoje.isoformat(), situacao="limpar")
        self.assertFalse(self.conta.usos.exists())

    def test_tela_recusa_futuro_e_parte_sem_valor(self):
        self.post(data="2099-01-01", situacao="nada")
        self.post(data=date.today().isoformat(), situacao="parte")
        self.post(data="2026-10-31", situacao="nada")  # antes do início
        self.assertFalse(UsoDiario.objects.exists())

    def test_tela_de_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        r = self.client.post(reverse("financas:uso_diario", args=[outra.pk]),
                             {"data": "2026-01-02", "situacao": "nada"})
        self.assertEqual(r.status_code, 404)


class AbasPainelTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("600"))
        self.url = reverse("financas:painel", args=[self.conta.pk])

    def test_cinco_abas(self):
        r = self.client.get(self.url)
        for aba in ['id="extrato"', 'id="contas"', 'id="cartoes"', 'id="investimentos"', 'id="conferencias"',
                    'role="tablist"']:
            self.assertContains(r, aba)
        self.assertContains(r, "Comece a guardar todo mês")   # sem guardar configurado

    def test_lancamento_pela_aba_cartoes_volta_para_ela(self):
        l = Lancamento.objects.create(conta=self.conta, data=date(2026, 2, 3), descricao="Freela", valor=300,
                                      tipo=Lancamento.ENTRADA)
        r = self.client.post(reverse("financas:item_editar", args=["lancamento", l.pk]) + "?voltar=cartoes",
                             {"tipo": "entrada", "descricao": "Freela", "valor": "350", "data": "2026-02-03"})
        self.assertRedirects(r, self.url + "#cartoes", fetch_redirect_response=False)
        r = self.client.post(reverse("financas:item_excluir", args=["lancamento", l.pk]), {"voltar": "cartoes"})
        self.assertRedirects(r, self.url + "#cartoes", fetch_redirect_response=False)

    def test_totais_das_contas_fixas(self):
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=4000, dia=5, tipo=ContaFixa.RECEITA)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=1500, dia=10)
        ContaFixa.objects.create(conta=self.conta, nome="Pausada", valor=999, dia=10, ativa=False)
        totais = self.client.get(self.url).context["totais_fixas"]
        self.assertEqual(totais, {"receitas": 4000, "despesas": 1500, "assinaturas": 0, "dia_a_dia": 600,
                                  "guardar": 0, "saidas": 2100, "sobra": Decimal("1900")})

    def test_dinheiro_do_dia_a_dia_na_lista(self):
        hoje = date.today()
        self.conta.data_inicio = hoje.replace(day=1)
        self.conta.save()
        if hoje.day > 1:
            UsoDiario.objects.create(conta=self.conta, data=hoje.replace(day=1), situacao=UsoDiario.NADA)
        r = self.client.get(self.url)
        dd = r.context["dia_a_dia"]
        self.assertEqual(dd["mensal"], Decimal("600"))
        self.assertEqual(dd["mes_total"], Decimal("600"))
        self.assertEqual(dd["usado"] + dd["restante"], Decimal("600"))
        esperado = sum((d.gasto for d in simular_mes(self.conta, hoje.year, hoje.month) if d.data <= hoje), Decimal("0"))
        self.assertEqual(dd["usado"], esperado)
        self.assertContains(r, "Dinheiro do dia a dia")
        self.assertContains(r, reverse("financas:conta_editar", args=[self.conta.pk]))

    def test_assinaturas_entram_na_lista_e_nos_totais(self):
        hoje = date.today()
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=4000, dia=5, tipo=ContaFixa.RECEITA)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=1500, dia=10)
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        AssinaturaCartao.objects.create(cartao=cartao, descricao="Netflix", valor=Decimal("44.90"),
                                        dia_cobranca=8, inicio=hoje - timedelta(days=60))
        AssinaturaCartao.objects.create(cartao=cartao, descricao="Disney+", valor=Decimal("33.90"), dia_cobranca=1,
                                        inicio=hoje - timedelta(days=200), fim=hoje - timedelta(days=30))
        r = self.client.get(self.url)
        itens = [(i["tipo"], i["nome"], i["inativa"]) for i in r.context["recorrentes"]]
        self.assertEqual(itens, [("fixa", "Salário", False), ("assinatura", "Netflix", False),
                                 ("fixa", "Aluguel", False), ("assinatura", "Disney+", True)])
        totais = r.context["totais_fixas"]
        self.assertEqual(totais["despesas"], Decimal("1544.90"))   # cancelada não entra
        self.assertEqual(totais["sobra"], Decimal("4000") - Decimal("1544.90") - 600)
        self.assertContains(r, "cartão Roxinho")
        self.assertContains(r, reverse("financas:assinatura_editar", args=[AssinaturaCartao.objects.get(descricao="Netflix").pk]))

    def test_voltar_para_a_aba_certa(self):
        r = self.client.post(reverse("financas:lancamento_novo", args=[self.conta.pk]),
                             {"data": "2026-02-03", "descricao": "Freela", "valor": "300", "tipo": "entrada"})
        self.assertRedirects(r, self.url + "?mes=2026-02&dia=2026-02-03#extrato", fetch_redirect_response=False)
        conf = Conferencia.objects.create(conta=self.conta, data=date(2026, 2, 3), saldo_real=900)
        r = self.client.post(reverse("financas:recomecar", args=[conf.pk]))
        self.assertRedirects(r, self.url + "#conferencias", fetch_redirect_response=False)
        fixa = ContaFixa.objects.create(conta=self.conta, nome="Internet", valor=100, dia=10)
        r = self.client.post(reverse("financas:item_excluir", args=["fixa", fixa.pk]))
        self.assertRedirects(r, self.url + "#contas", fetch_redirect_response=False)


class LancamentoFormTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # janeiro: 310/mês = 10/dia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"))
        self.url = reverse("financas:lancamento_novo", args=[self.conta.pk])

    def test_tela_nova_com_resumo_do_dia(self):
        r = self.client.get(self.url + "?data=2026-01-05")
        self.assertTemplateUsed(r, "financas/lancamento_form.html")
        self.assertEqual(r.context["base_data"], date(2026, 1, 5))
        self.assertEqual(r.context["base_esperado"], Decimal("950"))  # 1000 - 5 dias × 10

    def test_dia_antes_do_inicio_sem_resumo(self):
        r = self.client.get(self.url + "?data=2025-12-20")
        self.assertNotIn("base_data", r.context)

    def test_valor_precisa_ser_positivo(self):
        r = self.client.post(self.url, {"tipo": "saida", "descricao": "X", "valor": "0", "data": "2026-01-05"})
        self.assertContains(r, "Informe um valor maior que zero")
        self.assertFalse(Lancamento.objects.exists())

    def test_edicao_nao_conta_o_proprio_lancamento(self):
        l = Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 5), descricao="Conserto",
                                      valor=Decimal("200"), tipo=Lancamento.SAIDA)
        url = reverse("financas:item_editar", args=["lancamento", l.pk])
        self.assertEqual(self.client.get(url).context["base_esperado"], Decimal("950"))
        # mesmo com um POST inválido (valor alterado na instância), a base continua a salva
        r = self.client.post(url, {"tipo": "saida", "descricao": "", "valor": "999", "data": "2026-01-05"})
        self.assertEqual(r.context["base_esperado"], Decimal("950"))


class EscalaCoresTests(TestCase):
    """Verdes ≥ reserva > amarelo ≥ ½ > laranja ≥ ¼ > vermelho claro ≥ 0 > vermelho > −½ ≥ vermelho escuro."""

    def test_limites_das_faixas(self):
        reserva, ref = Decimal("800"), Decimal("4000")   # ¼ = 200, ½ = 400; verdes em faixas de 1000
        casos = {"-500": "n5", "-400": "n5", "-399": "n4", "-1": "n4", "0": "n3", "199": "n3",
                 "200": "n2", "399": "n2", "400": "n1", "799.99": "n1",
                 "800": "p1", "1799": "p1", "1800": "p2", "2800": "p3", "3800": "p4", "99999": "p4"}
        for valor, esperado in casos.items():
            self.assertEqual(nivel_cor(Decimal(valor), reserva, ref), esperado, valor)

    def test_sem_reserva(self):
        self.assertEqual(nivel_cor(Decimal("0"), Decimal("0"), Decimal("4000")), "p1")
        self.assertEqual(nivel_cor(Decimal("-10"), Decimal("0"), Decimal("4000")), "n4")
        self.assertEqual(nivel_cor(Decimal("-600"), Decimal("0"), Decimal("4000")), "n5")

    def test_modos_da_reserva(self):
        familia = Familia.objects.create(nome="T")
        conta = Conta.objects.create(familia=familia, nome="C", saldo_inicial=0, data_inicio=date(2026, 1, 1),
                                     modo_gasto=Conta.MENSAL, gasto_mensal=Decimal("1500"))
        ContaFixa.objects.create(conta=conta, nome="Salário", valor=Decimal("6000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        conta = Conta.objects.get(pk=conta.pk)
        self.assertEqual((conta.reserva, conta.quase_acabando), (Decimal("500"), Decimal("125")))   # automática
        conta.reserva_modo, conta.reserva_minima = Conta.RESERVA_FIXO, Decimal("800")
        self.assertEqual(conta.reserva, Decimal("800"))
        conta.reserva_modo, conta.reserva_percentual = Conta.RESERVA_PERCENTUAL, Decimal("15")
        self.assertEqual(conta.reserva, Decimal("900"))   # 15% de 6000

    def test_painel_mostra_mapa_cores_e_alerta(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        hoje = date.today()
        # 3000/mês de dia a dia (100/dia): reserva automática 1000, vermelho abaixo de 250
        conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1500"),
                                     data_inicio=hoje, gasto_mensal=Decimal("3000"))
        r = self.client.get(reverse("financas:painel", args=[conta.pk]))
        self.assertContains(r, "Quase")
        dia = r.context["dia"]
        self.assertEqual(dia.cor, nivel_cor(dia.esperado, conta.reserva, r.context["legenda"]["referencia"]))
        self.assertEqual(r.context["legenda"]["quase"], Decimal("250"))
        alerta = r.context["alerta"]
        serie = serie_diaria(conta, hoje, hoje + timedelta(days=60))
        primeiro = lambda limite: hoje + timedelta(days=next(i for i, v in enumerate(serie) if v < limite))
        self.assertEqual(alerta["abaixo_em"], primeiro(Decimal("1000")))
        self.assertEqual(alerta["quase_em"], primeiro(Decimal("250")))
        self.assertEqual(alerta["negativo_em"], primeiro(Decimal("0")))
        self.assertLess(alerta["abaixo_em"], alerta["quase_em"])
        self.assertLess(alerta["quase_em"], alerta["negativo_em"])     # avisa bem antes de ficar negativo
        self.assertContains(r, "quase sem dinheiro")

    def test_sem_alerta_quando_folgado(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("50000"),
                                     data_inicio=date.today(), gasto_mensal=Decimal("300"))
        self.assertIsNone(self.client.get(reverse("financas:painel", args=[conta.pk])).context["alerta"])

    def test_reserva_no_formulario(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("0"),
                                     data_inicio=date(2026, 1, 1), modo_gasto=Conta.MENSAL, gasto_mensal=Decimal("1500"))
        ContaFixa.objects.create(conta=conta, nome="Salário", valor=Decimal("6000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        url = reverse("financas:conta_editar", args=[conta.pk])
        dados = campos(self.client.get(url), "form-conta")
        self.assertEqual(dados["reserva_modo"], "auto")
        self.client.post(url, {**dados, "reserva_modo": "fixo", "reserva_minima": "1200", "reserva_percentual": "9"})
        conta = Conta.objects.get(pk=conta.pk)
        self.assertEqual((conta.reserva_modo, conta.reserva, conta.reserva_percentual), ("fixo", Decimal("1200"), None))
        self.client.post(url, {**dados, "reserva_modo": "percentual", "reserva_percentual": "10", "reserva_minima": ""})
        conta = Conta.objects.get(pk=conta.pk)
        self.assertEqual((conta.reserva_modo, conta.reserva, conta.reserva_minima), ("percentual", Decimal("600"), None))
        r = self.client.post(url, {**dados, "reserva_modo": "fixo", "reserva_minima": "-5"})
        self.assertContains(r, "não pode ser negativa")
        r = self.client.post(url, {**dados, "reserva_modo": "percentual", "reserva_percentual": "150"})
        self.assertContains(r, "entre 0 e 100")
        self.client.post(url, {**dados, "reserva_modo": "auto"})
        self.assertEqual(Conta.objects.get(pk=conta.pk).reserva, Decimal("500"))

class NavegacaoMapaTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # janeiro: 3100/mês = 100/dia; começa com 1000, fica negativo a partir do dia 11
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("3100"))
        self.url = reverse("financas:painel", args=[self.conta.pk])

    def test_resumo_do_mes_passado(self):
        r = self.client.get(self.url + "?mes=2026-01")
        resumo = r.context["resumo_mes"]
        self.assertEqual(resumo["inicio"], Decimal("1000"))
        self.assertEqual(resumo["fim"], Decimal("-2100"))
        self.assertEqual(resumo["variacao"], Decimal("-3100"))
        self.assertEqual(resumo["menor"].data, date(2026, 1, 31))
        self.assertEqual(resumo["dias_negativos"], 21)   # dias 11 a 31
        self.assertTrue(resumo["encerrado"])
        self.assertContains(r, "Terminou com")

    def test_nao_volta_antes_do_inicio(self):
        self.assertFalse(self.client.get(self.url + "?mes=2026-01").context["pode_voltar"])
        self.assertTrue(self.client.get(self.url + "?mes=2026-02").context["pode_voltar"])
        r = self.client.get(self.url + "?mes=2025-11")
        self.assertIsNone(r.context["resumo_mes"])
        self.assertContains(r, "anterior ao início do cálculo")

    def test_mes_futuro_abre_no_dia_1(self):
        futuro = date.today().replace(day=1) + timedelta(days=400)
        r = self.client.get(self.url + f"?mes={futuro:%Y-%m}")
        self.assertEqual(r.context["dia"].data, futuro.replace(day=1))
        self.assertContains(r, "Deve terminar com")


class SeletorMesTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # começa em 15/03/2026 com 1000; 3100/mês de dia a dia e salário de 3000 no dia 1
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 3, 15), gasto_mensal=Decimal("3100"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=3000, dia=1, tipo=ContaFixa.RECEITA)

    def test_meses_com_fechamento_e_cor(self):
        anos = meses_do_seletor(self.conta, 2026, 2026, date(2026, 5, 10))
        meses = anos[0]["meses"]
        self.assertEqual(len(meses), 12)
        self.assertFalse(meses[1]["ativo"])                 # fevereiro: antes do início
        self.assertTrue(meses[2]["ativo"])                  # março: começa no meio
        self.assertEqual(meses[2]["fim"], saldo_esperado(self.conta, date(2026, 3, 31)).esperado)
        self.assertTrue(meses[4]["aberto"])                 # maio é o mês de "hoje"
        self.assertTrue(meses[5]["futuro"])
        self.assertTrue(all(m.get("cor") for m in meses if m["ativo"]))

    def test_painel_tem_seletor_com_anos(self):
        url = reverse("financas:painel", args=[self.conta.pk])
        r = self.client.get(url + "?mes=2026-04")
        anos = [a["ano"] for a in r.context["anos_seletor"]]
        self.assertEqual(anos[0], 2026)
        self.assertEqual(anos[-1], date.today().year + 1)
        self.assertContains(r, 'id="seletor-mes"')
        self.assertContains(r, 'href="?mes=2026-07#extrato"')


class EditarExcluirLancamentoTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"))
        self.lanc = Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 5), descricao="Conserto",
                                              valor=Decimal("200"), tipo=Lancamento.SAIDA)
        self.painel = reverse("financas:painel", args=[self.conta.pk])
        self.excluir = reverse("financas:item_excluir", args=["lancamento", self.lanc.pk])
        self.editar = reverse("financas:item_editar", args=["lancamento", self.lanc.pk])

    def test_painel_mostra_acoes_no_dia(self):
        r = self.client.get(self.painel + "?mes=2026-01&dia=2026-01-05")
        self.assertContains(r, self.excluir)
        self.assertContains(r, self.editar)

    def test_excluir_volta_para_o_dia(self):
        r = self.client.post(self.excluir, follow=True)
        self.assertRedirects(r, self.painel + "?mes=2026-01&dia=2026-01-05#extrato")
        self.assertFalse(Lancamento.objects.exists())
        self.assertContains(r, "Conserto")  # mensagem de confirmação
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 5)).esperado, Decimal("950"))

    def test_excluir_pela_aba_contas_volta_para_ela(self):
        r = self.client.post(self.excluir, {"voltar": "contas"})
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)

    def test_editar_pela_aba_contas_volta_para_ela(self):
        r = self.client.post(self.editar + "?voltar=contas",
                             {"tipo": "saida", "descricao": "Conserto do carro", "valor": "250", "data": "2026-01-06"})
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)
        self.lanc.refresh_from_db()
        self.assertEqual((self.lanc.descricao, self.lanc.valor, self.lanc.data),
                         ("Conserto do carro", Decimal("250"), date(2026, 1, 6)))

    def test_editar_pelo_dia_volta_para_o_novo_dia(self):
        r = self.client.post(self.editar, {"tipo": "entrada", "descricao": "Reembolso", "valor": "80",
                                           "data": "2026-01-09"})
        self.assertRedirects(r, self.painel + "?mes=2026-01&dia=2026-01-09#extrato", fetch_redirect_response=False)

    def test_excluir_exige_post_e_mesma_familia(self):
        self.assertEqual(self.client.get(self.excluir).status_code, 405)
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        alheio = Lancamento.objects.create(conta=outra, data=date(2026, 1, 2), descricao="X", valor=1)
        r = self.client.post(reverse("financas:item_excluir", args=["lancamento", alheio.pk]))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Lancamento.objects.filter(pk=alheio.pk).exists())


class CartaoCalculoTests(TestCase):
    def setUp(self):
        familia = Familia.objects.create(nome="Teste")
        # 0/mês de dia a dia para isolar o efeito das faturas
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        # fecha 25, vence 5 do mês seguinte
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)

    def compra(self, valor, parcelas, data, cartao=None):
        return CompraCartao.objects.create(cartao=cartao or self.cartao, descricao="X", valor_total=Decimal(valor),
                                           parcelas=parcelas, data_compra=data)

    def test_regra_do_fechamento(self):
        self.assertEqual(primeira_fatura(self.cartao, date(2026, 3, 24)), (2026, 3))  # antes do fechamento
        self.assertEqual(primeira_fatura(self.cartao, date(2026, 3, 25)), (2026, 4))  # no dia: próxima
        self.assertEqual(primeira_fatura(self.cartao, date(2026, 12, 30)), (2027, 1))  # vira o ano

    def test_vencimento_no_mes_seguinte_ou_no_mesmo(self):
        self.assertEqual(datas_da_fatura(self.cartao, 2026, 3), (date(2026, 3, 25), date(2026, 4, 5)))
        outro = Cartao(conta=self.conta, nome="B", dia_fechamento=3, dia_vencimento=10)
        self.assertEqual(datas_da_fatura(outro, 2026, 3), (date(2026, 3, 3), date(2026, 3, 10)))
        fim_de_mes = Cartao(conta=self.conta, nome="C", dia_fechamento=31, dia_vencimento=8)
        self.assertEqual(datas_da_fatura(fim_de_mes, 2026, 2), (date(2026, 2, 28), date(2026, 3, 8)))

    def test_parcelas_somam_o_total(self):
        c = self.compra("1000", 3, date(2026, 3, 10))
        self.assertEqual(c.valores_parcelas(), [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")])
        self.assertEqual(sum(c.valores_parcelas()), Decimal("1000"))

    def test_cronograma(self):
        c = self.compra("300", 3, date(2026, 3, 26))  # depois do fechamento: 1ª na fatura de abril
        vencimentos = [p.vencimento for p in cronograma(c)]
        self.assertEqual(vencimentos, [date(2026, 5, 5), date(2026, 6, 5), date(2026, 7, 5)])
        self.assertEqual([p.rotulo for p in cronograma(c)], ["1/3", "2/3", "3/3"])

    def test_saldo_esperado_desconta_no_vencimento(self):
        self.compra("600", 3, date(2026, 3, 10))   # 200 em 05/04, 05/05, 05/06
        self.compra("50", 1, date(2026, 3, 20))    # 50 em 05/04
        self.assertEqual(saldo_esperado(self.conta, date(2026, 4, 4)).esperado, Decimal("5000"))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 4, 5)).esperado, Decimal("4750"))
        r = saldo_esperado(self.conta, date(2026, 6, 30))
        self.assertEqual((r.faturas, r.esperado), (Decimal("650"), Decimal("4350")))
        fat = faturas(self.conta, date(2026, 4, 1), date(2026, 4, 30))
        self.assertEqual(len(fat), 1)
        self.assertEqual((fat[0].nome, fat[0].total, len(fat[0].parcelas)), ("Fatura Roxinho", Decimal("250"), 2))

    def test_extrato_mostra_fatura_e_bate(self):
        self.compra("600", 3, date(2026, 3, 10))
        dias = extrato_mes(self.conta, 2026, 4)
        movs = dias[4].movimentos
        self.assertEqual([(m.nome, m.valor, m.origem) for m in movs], [("Fatura Roxinho", Decimal("200"), "fatura")])
        for d in dias:
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado)

    def test_compra_antiga_so_conta_parcelas_depois_do_inicio(self):
        self.compra("1200", 12, date(2025, 6, 10))  # parcelas de 100 de 05/07/2025 a 05/06/2026
        self.assertEqual(saldo_esperado(self.conta, date(2026, 12, 31)).faturas, Decimal("600"))  # jan a jun


class CartaoTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def test_compra_sem_cartao_leva_ao_cadastro_e_volta(self):
        r = self.client.get(reverse("financas:compra_nova", args=[self.conta.pk]))
        url_cartao = reverse("financas:cartao_novo", args=[self.conta.pk]) + "?proximo=compra"
        self.assertRedirects(r, url_cartao)
        r = self.client.post(url_cartao, {"nome": "Roxinho", "dia_fechamento": "25", "dia_vencimento": "5"})
        cartao = Cartao.objects.get()
        self.assertRedirects(r, reverse("financas:compra_nova", args=[self.conta.pk]) + f"?cartao={cartao.pk}")

    def test_lancar_editar_e_excluir_compra(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        url = reverse("financas:compra_nova", args=[self.conta.pk])
        self.assertTemplateUsed(self.client.get(url), "financas/compra_form.html")
        r = self.client.post(url, {"cartao": cartao.pk, "descricao": "Geladeira", "valor_total": "3000",
                                   "parcelas": "10", "data_compra": "2026-03-10"})
        self.assertRedirects(r, self.painel + "#cartoes", fetch_redirect_response=False)
        compra = CompraCartao.objects.get()
        self.assertEqual((compra.parcelas, compra.valor_total), (10, Decimal("3000")))
        painel = self.client.get(self.painel + "?mes=2026-04&dia=2026-04-05")
        self.assertContains(painel, "Fatura Roxinho")
        self.assertContains(painel, "Geladeira")

        self.client.post(reverse("financas:compra_editar", args=[compra.pk]),
                         {"cartao": cartao.pk, "descricao": "Geladeira", "valor_total": "3000",
                          "parcelas": "12", "data_compra": "2026-03-10"})
        compra.refresh_from_db()
        self.assertEqual(compra.parcelas, 12)
        self.client.post(reverse("financas:compra_excluir", args=[compra.pk]))
        self.assertFalse(CompraCartao.objects.exists())

    def test_validacoes(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        r = self.client.post(reverse("financas:compra_nova", args=[self.conta.pk]),
                             {"cartao": cartao.pk, "descricao": "X", "valor_total": "0", "parcelas": "60",
                              "data_compra": "2026-03-10"})
        self.assertContains(r, "Informe um valor maior que zero")
        self.assertContains(r, "No máximo 48 parcelas")
        r = self.client.post(reverse("financas:cartao_novo", args=[self.conta.pk]),
                             {"nome": "Y", "dia_fechamento": "40", "dia_vencimento": "0"})
        self.assertContains(r, "Use um dia entre 1 e 31")

    def test_nao_usa_cartao_de_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        alheio = Cartao.objects.create(conta=outra, nome="Deles", dia_fechamento=1, dia_vencimento=10)
        Cartao.objects.create(conta=self.conta, nome="Meu", dia_fechamento=25, dia_vencimento=5)
        r = self.client.post(reverse("financas:compra_nova", args=[self.conta.pk]),
                             {"cartao": alheio.pk, "descricao": "X", "valor_total": "10", "parcelas": "1",
                              "data_compra": "2026-03-10"})
        self.assertEqual(r.status_code, 200)  # formulário recusa o cartão
        self.assertFalse(CompraCartao.objects.exists())
        self.assertEqual(self.client.get(reverse("financas:cartao_editar", args=[alheio.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:cartao_excluir", args=[alheio.pk])).status_code, 404)


class ImpactoCompraTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        hoje = date.today()
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=hoje.replace(day=1), gasto_mensal=Decimal("600"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)

    def test_serie_bate_com_saldo_esperado(self):
        inicio = self.conta.data_inicio
        fim = inicio + timedelta(days=75)
        serie = serie_diaria(self.conta, inicio, fim)
        self.assertEqual(len(serie), 76)
        for i in (0, 10, 40, 75):
            self.assertEqual(serie[i], saldo_esperado(self.conta, inicio + timedelta(days=i)).esperado)

    def test_tela_envia_projecao(self):
        r = self.client.get(reverse("financas:compra_nova", args=[self.conta.pk]))
        proj = r.context["projecao"]
        self.assertEqual(proj["inicio"], date.today().isoformat())
        self.assertEqual(proj["salario"], 4000.0)
        self.assertGreater(len(proj["centavos"]), 24 * 28)
        self.assertEqual(proj["centavos"][0], int(saldo_esperado(self.conta, date.today()).esperado * 100))
        self.assertContains(r, 'id="dados-projecao"')

    def test_edicao_projeta_sem_a_propria_compra(self):
        nova = self.client.get(reverse("financas:compra_nova", args=[self.conta.pk])).context["projecao"]
        compra = CompraCartao.objects.create(cartao=self.cartao, descricao="TV", valor_total=Decimal("1200"),
                                             parcelas=3, data_compra=date.today())
        edicao = self.client.get(reverse("financas:compra_editar", args=[compra.pk])).context["projecao"]
        self.assertEqual(edicao["centavos"], nova["centavos"])


class AssinaturaCalculoTests(TestCase):
    def setUp(self):
        familia = Familia.objects.create(nome="Teste")
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)

    def assinatura(self, valor="40", dia=15, inicio=date(2026, 1, 1), fim=None):
        return AssinaturaCartao.objects.create(cartao=self.cartao, descricao="Netflix", valor=Decimal(valor),
                                               dia_cobranca=dia, inicio=inicio, fim=fim)

    def test_cobrancas_caem_na_fatura_certa(self):
        a = self.assinatura(dia=15)
        cs = cobrancas(a, date(2026, 2, 1), date(2026, 4, 30))
        # cobra 15/01 → fatura de jan (fecha 25/01) vence 05/02; 15/02 → 05/03; 15/03 → 05/04
        self.assertEqual([(c.data, c.vencimento) for c in cs],
                         [(date(2026, 1, 15), date(2026, 2, 5)), (date(2026, 2, 15), date(2026, 3, 5)),
                          (date(2026, 3, 15), date(2026, 4, 5))])
        depois_do_fechamento = self.assinatura(dia=28)
        self.assertEqual(cobrancas(depois_do_fechamento, date(2026, 3, 1), date(2026, 3, 31))[0].data,
                         date(2026, 1, 28))  # 28/01 já cai na fatura de fevereiro, que vence 05/03

    def test_inicio_fim_e_dia_31(self):
        # cancelada a partir de 01/05: abril ainda cobra, maio não
        a = self.assinatura(dia=31, inicio=date(2026, 2, 10), fim=date(2026, 5, 1))
        datas = [c.data for c in cobrancas(a, date(2026, 1, 1), date(2026, 12, 31))]
        self.assertEqual(datas, [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        # cancelada exatamente no dia da cobrança: não cobra nesse dia
        b = self.assinatura(dia=15, inicio=date(2026, 1, 1), fim=date(2026, 3, 15))
        self.assertEqual([c.data for c in cobrancas(b, date(2026, 1, 1), date(2026, 12, 31))],
                         [date(2026, 1, 15), date(2026, 2, 15)])

    def test_entra_no_saldo_esperado_e_na_fatura(self):
        self.assinatura(valor="40", dia=15)
        CompraCartao.objects.create(cartao=self.cartao, descricao="TV", valor_total=Decimal("300"),
                                    parcelas=3, data_compra=date(2026, 1, 10))
        f = faturas(self.conta, date(2026, 2, 5), date(2026, 2, 5))[0]
        self.assertEqual((f.total, f.quantidade, len(f.cobrancas)), (Decimal("140"), 2, 1))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 2, 5)).esperado, Decimal("860"))
        for d in extrato_mes(self.conta, 2026, 2):
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado)

    def test_proximo_fechamento(self):
        self.assertEqual(proximo_fechamento(self.cartao, date(2026, 3, 10)), (date(2026, 3, 25), date(2026, 4, 5)))
        self.assertEqual(proximo_fechamento(self.cartao, date(2026, 3, 25)), (date(2026, 3, 25), date(2026, 4, 5)))
        self.assertEqual(proximo_fechamento(self.cartao, date(2026, 3, 26)), (date(2026, 4, 25), date(2026, 5, 5)))


class AssinaturaTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date.today().replace(day=1), gasto_mensal=Decimal("0"))
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def test_sem_cartao_leva_ao_cadastro_e_volta(self):
        url = reverse("financas:assinatura_nova", args=[self.conta.pk])
        self.assertRedirects(self.client.get(url), reverse("financas:cartao_novo", args=[self.conta.pk]) + "?proximo=assinatura")
        r = self.client.post(reverse("financas:cartao_novo", args=[self.conta.pk]) + "?proximo=assinatura",
                             {"nome": "Roxinho", "dia_fechamento": "25", "dia_vencimento": "5"})
        self.assertRedirects(r, url + f"?cartao={Cartao.objects.get().pk}")

    def test_criar_validar_e_listar(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        url = reverse("financas:assinatura_nova", args=[self.conta.pk])
        r = self.client.post(url, {"cartao": cartao.pk, "descricao": "Netflix", "valor": "44.90", "dia_cobranca": "10",
                                   "inicio": "2026-05-01", "fim": "2026-04-01"})
        self.assertContains(r, "não pode ser antes do início")
        r = self.client.post(url, {"cartao": cartao.pk, "descricao": "Netflix", "valor": "44.90",
                                   "dia_cobranca": "10", "inicio": date.today().isoformat(), "fim": ""})
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)
        painel = self.client.get(self.painel)
        self.assertContains(painel, "Netflix")
        self.assertEqual(painel.context["assinaturas"]["total_mes"], Decimal("44.90"))
        info = painel.context["cartoes"][0]
        self.assertEqual(info["fechamento"]["data"], proximo_fechamento(cartao, date.today())[0])

    def test_resumo_do_fechamento_no_painel(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        CompraCartao.objects.create(cartao=cartao, descricao="TV", valor_total=Decimal("200"), parcelas=1,
                                    data_compra=date.today())
        fech, venc = proximo_fechamento(cartao, date.today())
        info = self.client.get(self.painel).context["cartoes"][0]["fechamento"]
        self.assertEqual(info["fatura"], Decimal("200"))
        self.assertEqual(info["saldo"], saldo_esperado(self.conta, fech).esperado)
        self.assertEqual(info["saldo_apos"], saldo_esperado(self.conta, venc).esperado)

    def test_projecao_da_compra_traz_faturas_sem_a_propria_compra(self):
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        AssinaturaCartao.objects.create(cartao=cartao, descricao="Spotify", valor=Decimal("20"),
                                        dia_cobranca=1, inicio=date.today())
        compra = CompraCartao.objects.create(cartao=cartao, descricao="TV", valor_total=Decimal("300"),
                                             parcelas=1, data_compra=date.today())
        proj = self.client.get(reverse("financas:compra_editar", args=[compra.pk])).context["projecao"]
        totais = proj["faturas"][str(cartao.pk)]
        self.assertTrue(all(v == 2000 for v in totais.values()))  # só a assinatura, sem a TV

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        c = Cartao.objects.create(conta=outra, nome="Deles", dia_fechamento=1, dia_vencimento=10)
        a = AssinaturaCartao.objects.create(cartao=c, descricao="X", valor=1, dia_cobranca=1, inicio=date(2026, 1, 1))
        self.assertEqual(self.client.get(reverse("financas:assinatura_editar", args=[a.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:assinatura_excluir", args=[a.pk])).status_code, 404)


class EditarExcluirContaFixaTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.fixa = ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10)
        self.painel = reverse("financas:painel", args=[self.conta.pk])
        self.editar = reverse("financas:item_editar", args=["fixa", self.fixa.pk])
        self.excluir = reverse("financas:item_excluir", args=["fixa", self.fixa.pk])
        self.dados = {"tipo": "despesa", "nome": "Aluguel", "valor": "1600", "dia": "12", "ativa": "on"}

    def test_botoes_na_aba_contas_e_no_dia(self):
        r = self.client.get(self.painel + "?mes=2026-01&dia=2026-01-10")
        self.assertContains(r, self.editar + "?voltar=contas")
        self.assertContains(r, self.editar + "?voltar=dia:2026-01-10")
        self.assertContains(r, self.excluir)

    def test_editar_pela_aba_contas(self):
        r = self.client.post(self.editar + "?voltar=contas", self.dados)
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)
        self.fixa.refresh_from_db()
        self.assertEqual((self.fixa.valor, self.fixa.dia), (Decimal("1600"), 12))

    def test_editar_pelo_dia_volta_para_o_dia(self):
        r = self.client.post(self.editar + "?voltar=dia:2026-01-10", self.dados)
        self.assertRedirects(r, self.painel + "?mes=2026-01&dia=2026-01-10#extrato", fetch_redirect_response=False)
        tela = self.client.get(self.editar + "?voltar=dia:2026-01-10")
        self.assertEqual(tela.context["url_voltar"], self.painel + "?mes=2026-01&dia=2026-01-10#extrato")

    def test_voltar_invalido_cai_no_padrao(self):
        r = self.client.post(self.editar + "?voltar=dia:xx", self.dados)
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)

    def test_excluir_pelo_dia_e_recalcula(self):
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 31)).esperado, Decimal("3500"))
        r = self.client.post(self.excluir, {"voltar": "dia:2026-01-10"}, follow=True)
        self.assertRedirects(r, self.painel + "?mes=2026-01&dia=2026-01-10#extrato")
        self.assertContains(r, "excluída de todos os meses")
        self.assertFalse(ContaFixa.objects.exists())
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 31)).esperado, Decimal("5000"))

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        alheia = ContaFixa.objects.create(conta=outra, nome="X", valor=1, dia=1)
        self.assertEqual(self.client.get(reverse("financas:item_editar", args=["fixa", alheia.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:item_excluir", args=["fixa", alheia.pk])).status_code, 404)
        self.assertTrue(ContaFixa.objects.filter(pk=alheia.pk).exists())


class ContaVariavelTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.luz = ContaFixa.objects.create(conta=self.conta, nome="Luz", valor=Decimal("200"), dia=10, variavel=True)
        self.painel = reverse("financas:painel", args=[self.conta.pk])
        self.pagar = reverse("financas:pagamento_registrar", args=[self.luz.pk])

    def pagar_em(self, referencia, valor, data):
        return PagamentoConta.objects.create(fixa=self.luz, referencia=referencia, valor=Decimal(valor), data=data)

    def test_estimativa_ate_pagar(self):
        self.assertEqual(saldo_esperado(self.conta, date(2026, 3, 31)).esperado, Decimal("4400"))  # 3 × 200
        self.pagar_em(date(2026, 2, 10), "187.40", date(2026, 2, 9))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 3, 31)).esperado, Decimal("4412.60"))
        e = [x for x in eventos_fixa(self.luz, date(2026, 2, 1), date(2026, 2, 28))][0]
        self.assertEqual((e.data, e.valor, e.estimado, e.vencimento), (date(2026, 2, 9), Decimal("187.40"), False,
                                                                      date(2026, 2, 10)))

    def test_pagamento_em_outro_mes_muda_a_data(self):
        self.pagar_em(date(2026, 1, 10), "210", date(2026, 2, 2))   # luz de janeiro paga em fevereiro
        self.assertEqual(saldo_esperado(self.conta, date(2026, 1, 31)).esperado, Decimal("5000"))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 2, 2)).esperado, Decimal("4790"))
        for d in extrato_mes(self.conta, 2026, 1) + extrato_mes(self.conta, 2026, 2):
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado, d.data)
        movs = extrato_mes(self.conta, 2026, 2)[1].movimentos
        self.assertEqual([(m.nome, m.valor, m.evento.estimado) for m in movs], [("Luz", Decimal("210"), False)])

    def test_pagas_saem_dos_proximos_vencimentos(self):
        hoje = date(2026, 3, 1)
        self.pagar_em(date(2026, 3, 10), "190", date(2026, 3, 1))
        prox = proximas_fixas(self.conta, hoje, 3)
        self.assertEqual([d for d, _ in prox], [date(2026, 4, 10), date(2026, 5, 10), date(2026, 6, 10)])
        self.assertTrue(all(e.estimado for _, e in prox))

    def test_marcar_corrigir_e_desfazer_pela_tela(self):
        r = self.client.post(self.pagar, {"referencia": "2026-02-10", "valor": "187.40", "data": "2026-02-09"})
        self.assertRedirects(r, self.painel + "?mes=2026-02&dia=2026-02-10#extrato", fetch_redirect_response=False)
        self.client.post(self.pagar, {"referencia": "2026-02-10", "valor": "190.00", "data": "2026-02-11",
                                      "voltar": "contas"})
        p = PagamentoConta.objects.get()
        self.assertEqual((p.valor, p.data), (Decimal("190.00"), date(2026, 2, 11)))   # corrigiu, não duplicou
        r = self.client.post(reverse("financas:pagamento_excluir", args=[p.pk]), {"voltar": "dia:2026-02-10"})
        self.assertRedirects(r, self.painel + "?mes=2026-02&dia=2026-02-10#extrato", fetch_redirect_response=False)
        self.assertFalse(PagamentoConta.objects.exists())

    def test_validacoes_e_permissoes(self):
        self.client.post(self.pagar, {"referencia": "2026-02-10", "valor": "0", "data": "2026-02-09"})
        self.assertFalse(PagamentoConta.objects.exists())
        # conta comum também aceita, como "ajuste só neste mês"
        fixa_comum = ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=1500, dia=5)
        r = self.client.post(reverse("financas:pagamento_registrar", args=[fixa_comum.pk]),
                             {"referencia": "2026-02-05", "valor": "1550", "data": "2026-02-05"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(fixa_comum.pagamentos.get().valor, Decimal("1550"))
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        alheia = ContaFixa.objects.create(conta=outra, nome="Luz", valor=100, dia=10, variavel=True)
        r = self.client.post(reverse("financas:pagamento_registrar", args=[alheia.pk]),
                             {"referencia": "2026-02-10", "valor": "90", "data": "2026-02-10"})
        self.assertEqual(r.status_code, 404)

    def test_tela_de_edicao_e_lista(self):
        self.pagar_em(date(2026, 1, 10), "180", date(2026, 1, 10))
        self.pagar_em(date(2026, 2, 10), "220", date(2026, 2, 10))
        r = self.client.get(reverse("financas:item_editar", args=["fixa", self.luz.pk]))
        self.assertEqual(r.context["media_pagamentos"], Decimal("200.00"))
        self.assertContains(r, "Usar como estimativa")
        r = self.client.post(reverse("financas:fixa_nova", args=[self.conta.pk]),
                             {"tipo": "despesa", "nome": "Mercado", "variavel": "on", "valor": "900", "dia": "1"})
        self.assertTrue(ContaFixa.objects.get(nome="Mercado").variavel)
        painel = self.client.get(self.painel)
        self.assertContains(painel, "varia")


class GuardarTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # janeiro: dia a dia 310 (10/dia); salário 4000 no dia 5; aluguel 1500 no dia 10
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("500"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10)

    def conta_nova(self):
        return Conta.objects.get(pk=self.conta.pk)   # sem o cache de cálculos

    def test_guarda_no_dia_de_maior_saldo(self):
        g = dia_de_guardar(self.conta, 2026, 1)
        self.assertEqual((g.data, g.valor), (date(2026, 1, 5), Decimal("500")))   # depois do salário

    def test_empate_fica_com_o_primeiro_dia(self):
        conta = self.conta_nova()
        conta.fixas.all().delete()
        conta.gasto_mensal = Decimal("0")
        self.assertEqual(dia_de_guardar(conta, 2026, 1).data, date(2026, 1, 1))

    def test_saldo_esperado_e_extrato(self):
        conta = self.conta_nova()
        self.assertEqual(saldo_esperado(conta, date(2026, 1, 4)).esperado, Decimal("960"))
        r = saldo_esperado(conta, date(2026, 1, 5))
        self.assertEqual((r.guardado, r.esperado), (Decimal("500"), Decimal("1000") - 50 + 4000 - 500))
        for d in extrato_mes(conta, 2026, 1) + extrato_mes(conta, 2026, 2):
            self.assertEqual(d.esperado, saldo_esperado(self.conta_nova(), d.data).esperado, d.data)
        dia5 = extrato_mes(conta, 2026, 1)[4]
        self.assertIn(("Guardar no mês", Decimal("500"), "guardar"), [(m.nome, m.valor, m.origem) for m in dia5.movimentos])
        self.assertEqual(len(guardados(conta, date(2026, 1, 1), date(2026, 3, 31))), 3)

    def test_percentual_do_salario(self):
        conta = self.conta_nova()
        conta.guardar_modo, conta.guardar_percentual, conta.guardar_valor = Conta.GUARDAR_PERCENTUAL, Decimal("10"), None
        self.assertEqual(conta.guardar_mensal, Decimal("400.00"))
        conta.guardar_modo = Conta.GUARDAR_NADA
        self.assertEqual(conta.guardar_mensal, 0)
        self.assertIsNone(dia_de_guardar(conta, 2026, 1))

    def test_formulario(self):
        url = reverse("financas:conta_editar", args=[self.conta.pk])
        base = {"nome": "Nubank", "saldo_inicial": "1000", "data_inicio": "2026-01-01", "modo_gasto": "mensal",
                "gasto_mensal": "310", **pagamentos(("5", "4000", self.conta.fixas.get(salario=True).pk), existentes=1)}
        r = self.client.post(url, {**base, "guardar_modo": "percentual", "guardar_percentual": "150"})
        self.assertContains(r, "entre 0 e 100")
        r = self.client.post(url, {**base, "guardar_modo": "fixo", "guardar_valor": ""})
        self.assertContains(r, "Informe quanto guardar por mês")
        self.client.post(url, {**base, "guardar_modo": "percentual", "guardar_percentual": "15", "guardar_valor": "999"})
        conta = self.conta_nova()
        self.assertEqual((conta.guardar_modo, conta.guardar_percentual, conta.guardar_valor),
                         ("percentual", Decimal("15"), None))
        self.assertEqual(conta.guardar_mensal, Decimal("600.00"))
        self.client.post(url, {**base, "guardar_modo": "nada"})
        self.assertEqual(self.conta_nova().guardar_mensal, 0)

    def test_painel(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]) + "?mes=2026-01&dia=2026-01-05")
        self.assertContains(r, "Guardar todo mês")
        self.assertContains(r, "dia de maior saldo")
        self.assertEqual(r.context["guardar"]["mensal"], Decimal("500"))
        self.assertEqual(r.context["totais_fixas"]["guardar"], Decimal("500"))


class InvestimentosTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        hoje = date.today()
        inicio = date(hoje.year - (1 if hoje.month <= 2 else 0), (hoje.month - 3) % 12 + 1, 1)  # ~2 meses atrás
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=inicio, gasto_mensal=Decimal("0"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("300"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("3000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)

    def test_historico_e_previsao(self):
        hoje = date.today()
        inv = self.client.get(reverse("financas:painel", args=[self.conta.pk])).context["investimentos"]
        linhas = inv["linhas"]
        self.assertTrue(all(l["valor"] == Decimal("300") for l in linhas))
        self.assertEqual([l["acumulado"] for l in linhas], [Decimal("300") * (i + 1) for i in range(len(linhas))])
        self.assertTrue(all(l["data"].day == 5 for l in linhas))      # dia do salário = maior saldo
        passados = [l for l in linhas if l["data"] <= hoje]
        self.assertEqual(inv["total_hoje"], Decimal("300") * len(passados))
        self.assertEqual(inv["confirmados"], 0)
        self.assertEqual(inv["pendentes"], len([l for l in linhas if l["referencia"] <= hoje.replace(day=1)]))
        self.assertGreater(inv["proximo"]["data"], hoje)
        self.assertEqual(inv["total_12"], linhas[-1]["acumulado"])
        self.assertGreaterEqual(len(linhas) - len(passados), 12)


from html.parser import HTMLParser


class _CamposDoFormulario(HTMLParser):
    """Lê os campos de um formulário como o navegador os enviaria (sem os <template>)."""

    def __init__(self, form_id):
        super().__init__()
        self.form_id, self.dados, self.dentro, self.template = form_id, {}, False, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == self.form_id:
            self.dentro = True
        if tag == "template":
            self.template += 1
        if not self.dentro or self.template or tag != "input" or "name" not in a:
            return
        if a.get("type") in ("radio", "checkbox"):
            if "checked" in a:
                self.dados[a["name"]] = a.get("value", "on")
        else:
            self.dados[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "template":
            self.template -= 1
        if tag == "form":
            self.dentro = False


def campos(resposta, form_id):
    p = _CamposDoFormulario(form_id)
    p.feed(resposta.content.decode())
    return p.dados


class EdicaoMostraDadosSalvosTests(TestCase):
    """Telas de edição precisam mostrar o que está salvo, não as sugestões de cadastro novo."""

    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Familia", saldo_inicial=Decimal("0"),
                                          data_inicio=date(2026, 1, 11), modo_gasto=Conta.MENSAL,
                                          gasto_mensal=Decimal("1500"))
        for dia, valor in [(15, "1850"), (30, "3850")]:
            ContaFixa.objects.create(conta=self.conta, nome=f"Salário (dia {dia})", valor=Decimal(valor), dia=dia,
                                     tipo=ContaFixa.RECEITA, salario=True)
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)

    def test_configurar_conta_mostra_o_salvo_e_salva_o_guardar(self):
        url = reverse("financas:conta_editar", args=[self.conta.pk])
        dados = campos(self.client.get(url), "form-conta")
        self.assertEqual(dados["modo_gasto"], "mensal")
        self.assertEqual(dados["gasto_mensal"], "1500.00")
        self.assertEqual(dados["data_inicio"], "2026-01-11")
        # a pessoa só muda o "guardar" e salva, como no navegador
        dados.update({"guardar_modo": "fixo", "guardar_valor": "500"})
        r = self.client.post(url, dados)
        self.assertEqual(r.status_code, 302, r.content.decode()[:500])
        conta = Conta.objects.get(pk=self.conta.pk)
        self.assertEqual((conta.modo_gasto, conta.gasto_mensal, conta.data_inicio),
                         ("mensal", Decimal("1500"), date(2026, 1, 11)))
        self.assertEqual((conta.guardar_modo, conta.guardar_mensal), ("fixo", Decimal("500")))
        self.assertEqual(conta.fixas.filter(salario=True).count(), 2)   # salário intacto

    def test_conta_nova_continua_com_sugestoes(self):
        r = self.client.get(reverse("financas:conta_nova"))
        self.assertEqual(r.context["form"]["modo_gasto"].value(), "percentual")
        self.assertEqual(r.context["form"]["data_inicio"].value(), date.today())

    def test_editar_compra_mostra_o_salvo(self):
        outro = Cartao.objects.create(conta=self.conta, nome="Azul", dia_fechamento=1, dia_vencimento=10)
        compra = CompraCartao.objects.create(cartao=outro, descricao="TV", valor_total=Decimal("3000"),
                                             parcelas=10, data_compra=date(2026, 3, 10))
        url = reverse("financas:compra_editar", args=[compra.pk])
        dados = campos(self.client.get(url), "form-compra")
        self.assertEqual((dados["parcelas"], dados["data_compra"], dados["cartao"]), ("10", "2026-03-10", str(outro.pk)))
        dados["descricao"] = "TV da sala"
        self.client.post(url, dados)
        compra.refresh_from_db()
        self.assertEqual((compra.descricao, compra.parcelas, compra.data_compra, compra.cartao_id),
                         ("TV da sala", 10, date(2026, 3, 10), outro.pk))

    def test_editar_assinatura_mostra_o_salvo(self):
        a = AssinaturaCartao.objects.create(cartao=self.cartao, descricao="Netflix", valor=Decimal("44.90"),
                                            dia_cobranca=8, inicio=date(2025, 1, 8))
        url = reverse("financas:assinatura_editar", args=[a.pk])
        dados = campos(self.client.get(url), "form-assinatura")
        self.assertEqual((dados["dia_cobranca"], dados["inicio"]), ("8", "2025-01-08"))
        dados["valor"] = "49.90"
        self.client.post(url, dados)
        a.refresh_from_db()
        self.assertEqual((a.valor, a.dia_cobranca, a.inicio), (Decimal("49.90"), 8, date(2025, 1, 8)))


class AporteTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # janeiro de 2026: salário 4000 no dia 5, guardar 500 (vai para o dia 5)
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("500"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_confirmado_substitui_a_previsao(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.MENSAL, referencia=date(2026, 1, 1),
                              data=date(2026, 1, 8), valor=Decimal("420"))
        conta = self.nova()
        g = dia_de_guardar(conta, 2026, 1)
        self.assertEqual((g.data, g.valor, g.confirmado, g.previsto, g.nome),
                         (date(2026, 1, 8), Decimal("420"), True, Decimal("500"), "Guardado no mês"))
        self.assertEqual(saldo_esperado(conta, date(2026, 1, 7)).esperado, Decimal("5000"))
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 1, 8)).esperado, Decimal("4580"))

    def test_confirmacao_em_outro_mes_e_extrato(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.MENSAL, referencia=date(2026, 1, 1),
                              data=date(2026, 2, 2), valor=Decimal("500"))
        conta = self.nova()
        self.assertEqual(saldo_esperado(conta, date(2026, 1, 31)).esperado, Decimal("5000"))
        dias = extrato_mes(self.nova(), 2026, 1) + extrato_mes(self.nova(), 2026, 2)
        for d in dias:
            self.assertEqual(d.esperado, saldo_esperado(self.nova(), d.data).esperado, d.data)
        nomes_dia_2_fev = [m.nome for m in extrato_mes(self.nova(), 2026, 2)[1].movimentos]
        self.assertIn("Guardado no mês", nomes_dia_2_fev)

    def test_avulso(self):
        Aporte.objects.create(conta=self.conta, tipo=Aporte.AVULSO, data=date(2026, 1, 20), valor=Decimal("150"),
                              descricao="Bônus")
        conta = self.nova()
        gs = guardados(conta, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([(g.nome, g.valor) for g in gs],
                         [("Guardar no mês", Decimal("500")), ("Guardado avulso: Bônus", Decimal("150"))])
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 1, 31)).guardado, Decimal("650"))

    def test_avulso_sem_guardar_configurado(self):
        conta = self.nova()
        conta.guardar_modo = Conta.GUARDAR_NADA
        conta.save()
        Aporte.objects.create(conta=conta, tipo=Aporte.AVULSO, data=date(2026, 1, 20), valor=Decimal("150"))
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 1, 31)).guardado, Decimal("150"))
        inv = self.client.get(self.painel).context["investimentos"]
        self.assertEqual(inv["mensal"], 0)
        self.assertEqual(inv["avulsos"], Decimal("150"))

    def test_telas_confirmar_corrigir_desfazer(self):
        url = reverse("financas:aporte_confirmar", args=[self.conta.pk])
        r = self.client.post(url, {"referencia": "2026-01-01", "valor": "450", "data": "2026-01-06"})
        self.assertRedirects(r, self.painel + "#investimentos", fetch_redirect_response=False)
        self.client.post(url, {"referencia": "2026-01-01", "valor": "480", "data": "2026-01-07"})
        a = Aporte.objects.get()
        self.assertEqual((a.valor, a.data, a.tipo), (Decimal("480"), date(2026, 1, 7), Aporte.MENSAL))  # corrigiu
        inv = self.client.get(self.painel).context["investimentos"]
        jan = [l for l in inv["linhas"] if l["referencia"] == date(2026, 1, 1)][0]
        self.assertEqual(jan["situacao"], "confirmado")
        self.client.post(reverse("financas:aporte_excluir", args=[a.pk]))
        self.assertFalse(Aporte.objects.exists())

    def test_validacoes(self):
        url = reverse("financas:aporte_confirmar", args=[self.conta.pk])
        self.client.post(url, {"referencia": "2026-01-01", "valor": "450", "data": "2026-03-10"})   # longe do mês
        self.client.post(url, {"referencia": "2026-01-01", "valor": "0", "data": "2026-01-06"})
        self.client.post(url, {"referencia": "2099-01-01", "valor": "450", "data": "2026-01-06"})   # mês futuro
        avulso = reverse("financas:aporte_avulso", args=[self.conta.pk])
        self.client.post(avulso, {"valor": "100", "data": "2025-12-31"})   # antes do início
        self.assertFalse(Aporte.objects.exists())
        self.client.post(avulso, {"valor": "100", "data": "2099-01-01", "descricao": ""})  # agendar é permitido
        self.assertEqual(Aporte.objects.get().tipo, Aporte.AVULSO)

    def test_outra_familia(self):
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        a = Aporte.objects.create(conta=outra, tipo=Aporte.AVULSO, data=date(2026, 1, 2), valor=1)
        self.assertEqual(self.client.post(reverse("financas:aporte_avulso", args=[outra.pk]),
                                          {"valor": "1", "data": "2026-01-02"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:aporte_excluir", args=[a.pk])).status_code, 404)
        self.assertTrue(Aporte.objects.filter(pk=a.pk).exists())


class ContaPeriodicaTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("5000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.carro = ContaFixa.objects.create(conta=self.conta, nome="Produtos do carro", valor=Decimal("150"),
                                              dia=10, frequencia=ContaFixa.DIAS, intervalo_dias=45,
                                              proxima=date(2026, 1, 10))
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def datas(self, inicio=date(2026, 1, 1), fim=date(2026, 6, 30)):
        return [e.data for e in eventos_fixa(ContaFixa.objects.get(pk=self.carro.pk), inicio, fim)]

    def test_a_cada_45_dias(self):
        self.assertEqual(self.datas(), [date(2026, 1, 10), date(2026, 2, 24), date(2026, 4, 10), date(2026, 5, 25)])
        self.assertEqual(self.carro.valor_mensal, Decimal("101.39"))   # 150 × 365 ÷ (12 × 45)

    def test_compra_atrasada_empurra_a_proxima(self):
        PagamentoConta.objects.create(fixa=self.carro, referencia=date(2026, 1, 10), valor=Decimal("180"),
                                      data=date(2026, 1, 20))
        self.assertEqual(self.datas(), [date(2026, 1, 20), date(2026, 3, 6), date(2026, 4, 20), date(2026, 6, 4)])
        e = eventos_fixa(ContaFixa.objects.get(pk=self.carro.pk), date(2026, 1, 1), date(2026, 1, 31))[0]
        self.assertEqual((e.valor, e.vencimento, e.pagamento is not None), (Decimal("180"), date(2026, 1, 10), True))

    def test_compra_adiantada(self):
        PagamentoConta.objects.create(fixa=self.carro, referencia=date(2026, 1, 10), valor=Decimal("150"),
                                      data=date(2026, 1, 5))
        self.assertEqual(self.datas(fim=date(2026, 3, 31)), [date(2026, 1, 5), date(2026, 2, 19)])

    def test_saldo_extrato_e_proximos(self):
        PagamentoConta.objects.create(fixa=self.carro, referencia=date(2026, 1, 10), valor=Decimal("180"),
                                      data=date(2026, 1, 20))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 3, 31)).esperado, Decimal("5000") - 180 - 150)
        for d in extrato_mes(self.conta, 2026, 1) + extrato_mes(self.conta, 2026, 3):
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado, d.data)
        prox = proximas_fixas(self.conta, date(2026, 2, 1), 2)
        self.assertEqual([d for d, _ in prox], [date(2026, 3, 6), date(2026, 4, 20)])

    def test_formulario(self):
        url = reverse("financas:fixa_nova", args=[self.conta.pk])
        r = self.client.post(url, {"tipo": "despesa", "nome": "Ração", "valor": "120", "frequencia": "dias"})
        self.assertContains(r, "Informe de quantos em quantos dias")
        self.assertContains(r, "Informe a data prevista da próxima vez")
        self.client.post(url, {"tipo": "despesa", "nome": "Ração", "valor": "120", "frequencia": "dias",
                               "intervalo_dias": "20", "proxima": "2026-02-03"})
        racao = ContaFixa.objects.get(nome="Ração")
        self.assertEqual((racao.frequencia, racao.intervalo_dias, racao.proxima, racao.dia),
                         ("dias", 20, date(2026, 2, 3), 3))
        r = self.client.post(url, {"tipo": "despesa", "nome": "Internet", "valor": "100", "frequencia": "mensal"})
        self.assertContains(r, "Escolha o dia do mês")
        self.client.post(url, {"tipo": "despesa", "nome": "Internet", "valor": "100", "frequencia": "mensal",
                               "dia": "10", "intervalo_dias": "5", "proxima": "2026-02-03"})
        internet = ContaFixa.objects.get(nome="Internet")
        self.assertEqual((internet.frequencia, internet.intervalo_dias, internet.proxima), ("mensal", None, None))

    def test_marcar_pela_tela_e_lista(self):
        url = reverse("financas:pagamento_registrar", args=[self.carro.pk])
        self.client.post(url, {"referencia": "2026-01-10", "valor": "170", "data": "2026-01-12"})
        self.client.post(url, {"referencia": "2026-01-10", "valor": "175", "data": "2026-01-12"})   # corrige
        p = PagamentoConta.objects.get()
        self.assertEqual((p.valor, p.referencia), (Decimal("175"), date(2026, 1, 10)))
        r = self.client.get(self.painel)
        item = [i for i in r.context["recorrentes"] if i["obj"].pk == self.carro.pk][0]
        self.assertEqual(item["ultima_compra"], p)
        self.assertContains(r, "a cada 45 dias")
        self.assertEqual(r.context["totais_fixas"]["despesas"], Decimal("101.39"))   # equivalente mensal


class TemaTests(TestCase):
    def test_escuro_por_padrao_e_botao(self):
        r = self.client.get(reverse("financas:entrar"))
        self.assertContains(r, 'data-tema="escuro"')
        self.assertContains(r, 'role="switch"')
        self.assertContains(r, 'aria-checked="false"')   # escuro = switch "tema claro" desligado

    def test_cookie_escolhe_o_tema(self):
        self.client.cookies["tema"] = "claro"
        r = self.client.get(reverse("financas:entrar"))
        self.assertContains(r, 'data-tema="claro"')
        self.assertContains(r, 'aria-checked="true"')
        self.client.cookies["tema"] = "<script>"
        self.assertContains(self.client.get(reverse("financas:entrar")), 'data-tema="escuro"')

    def test_botao_na_area_logada(self):
        cadastrar(self.client)
        r = self.client.get(reverse("financas:conta_nova"))
        self.assertContains(r, 'class="tema-switch"')


class VigenciaTests(TestCase):
    """Salário com aumento/redução previstos e contas fixas com começo e fim."""

    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # dia a dia = 20% do salário; guardar = 10% do salário
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 11, 1), modo_gasto=Conta.PERCENTUAL,
                                          percentual_gasto=Decimal("20"), guardar_modo=Conta.GUARDAR_PERCENTUAL,
                                          guardar_percentual=Decimal("10"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4500"), dia=5, tipo=ContaFixa.RECEITA,
                                 salario=True, vigente_ate=date(2026, 12, 31))
        ContaFixa.objects.create(conta=self.conta, nome="Salário novo", valor=Decimal("5000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True, vigente_desde=date(2027, 1, 1))

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_salario_muda_no_mes_do_aumento(self):
        conta = self.nova()
        self.assertEqual(conta.salario_em(date(2026, 12, 10)), Decimal("4500"))
        self.assertEqual(conta.salario_em(date(2027, 1, 10)), Decimal("5000"))
        self.assertEqual(conta.orcamento_mensal_em(date(2026, 12, 1)), Decimal("900"))
        self.assertEqual(conta.orcamento_mensal_em(date(2027, 1, 1)), Decimal("1000"))
        self.assertEqual(conta.guardar_mensal_em(date(2027, 1, 1)), Decimal("500.00"))
        self.assertEqual(sum(d.gasto for d in simular_mes(conta, 2027, 1)), Decimal("1000"))
        self.assertEqual(dia_de_guardar(conta, 2026, 12).valor, Decimal("450.00"))
        self.assertEqual(dia_de_guardar(self.nova(), 2027, 1).valor, Decimal("500.00"))

    def test_receitas_respeitam_o_periodo_e_extrato_bate(self):
        conta = self.nova()
        dez = [m for d in extrato_mes(conta, 2026, 12) for m in d.movimentos if m.origem == "fixa"]
        jan = [m for d in extrato_mes(self.nova(), 2027, 1) for m in d.movimentos if m.origem == "fixa"]
        self.assertEqual([(m.nome, m.valor) for m in dez], [("Salário", Decimal("4500"))])
        self.assertEqual([(m.nome, m.valor) for m in jan], [("Salário novo", Decimal("5000"))])
        for d in extrato_mes(self.nova(), 2026, 12) + extrato_mes(self.nova(), 2027, 1):
            self.assertEqual(d.esperado, saldo_esperado(self.nova(), d.data).esperado, d.data)

    def test_aluguel_encerrado_nao_muda_o_passado(self):
        aluguel = ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10)
        antes = saldo_esperado(self.nova(), date(2026, 12, 31)).esperado
        aluguel.vigente_ate = date(2026, 12, 31)
        aluguel.save()
        self.assertEqual(saldo_esperado(self.nova(), date(2026, 12, 31)).esperado, antes)   # passado intacto
        self.assertEqual([e.data for e in eventos_fixa(aluguel, date(2026, 11, 1), date(2027, 3, 31))],
                         [date(2026, 11, 10), date(2026, 12, 10)])

    def test_formulario_do_salario_com_periodo(self):
        url = reverse("financas:conta_editar", args=[self.conta.pk])
        dados = campos(self.client.get(url), "form-conta")
        self.assertEqual(dados["salario-0-vigente_ate"], "2026-12-31")
        self.assertEqual(dados["salario-1-vigente_desde"], "2027-01-01")
        dados["salario-1-vigente_ate"] = "2026-06-01"   # antes do início: inválido
        r = self.client.post(url, dados)
        self.assertContains(r, "não pode ser antes")
        dados["salario-1-vigente_ate"] = ""
        dados["salario-1-valor"] = "5200"
        self.client.post(url, dados)
        nomes = sorted(ContaFixa.objects.filter(conta=self.conta, salario=True).values_list("nome", "valor"))
        self.assertEqual(nomes, [("Salário (dia 5, a partir de 01/2027)", Decimal("5200")),
                                 ("Salário (dia 5, até 12/2026)", Decimal("4500"))])

    def test_conta_fixa_com_periodo_e_lista(self):
        url = reverse("financas:fixa_nova", args=[self.conta.pk])
        r = self.client.post(url, {"tipo": "despesa", "nome": "Escola", "valor": "700", "frequencia": "mensal",
                                   "dia": "10", "vigente_desde": "2027-02-01", "vigente_ate": "2027-01-01"})
        self.assertContains(r, "não pode ser antes")
        self.client.post(url, {"tipo": "despesa", "nome": "Escola", "valor": "700", "frequencia": "mensal",
                               "dia": "10", "vigente_desde": "2099-02-01"})
        escola = ContaFixa.objects.get(nome="Escola")
        self.assertEqual(escola.vigente_desde, date(2099, 2, 1))
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(r, "começa em 01/02/2099")
        self.assertEqual(r.context["totais_fixas"]["despesas"], 0)   # ainda não vale


class GastosDoDiaTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # novembro: 300/mês = 10/dia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 11, 1), gasto_mensal=Decimal("300"))

    def gasto(self, dia, valor, descricao=""):
        return GastoDia.objects.create(conta=self.conta, data=date(2026, 11, dia), valor=Decimal(valor),
                                       descricao=descricao)

    def test_soma_dos_gastos_e_o_uso_do_dia(self):
        self.gasto(1, "3.50", "Padaria")
        self.gasto(1, "2.50", "Ônibus")
        UsoDiario.objects.create(conta=self.conta, data=date(2026, 11, 1), situacao=UsoDiario.TUDO)  # ignorado
        dias = simular_mes(self.conta, 2026, 11)
        self.assertEqual((dias[0].gasto, len(dias[0].itens)), (Decimal("6.00"), 2))
        self.assertEqual(dias[1].disponivel, Decimal("10.14"))       # sobra de 4 diluída: 294 / 29
        self.assertEqual(sum(d.gasto for d in dias), Decimal("300"))
        for d in extrato_mes(self.conta, 2026, 11):
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado, d.data)
        self.assertEqual([g.descricao for g in extrato_mes(self.conta, 2026, 11)[0].itens], ["Padaria", "Ônibus"])

    def test_tela_adicionar_e_excluir(self):
        hoje = date.today()
        self.conta.data_inicio = hoje.replace(day=1)
        self.conta.save()
        url = reverse("financas:gasto_dia_adicionar", args=[self.conta.pk])
        painel = reverse("financas:painel", args=[self.conta.pk]) + f"?mes={hoje:%Y-%m}&dia={hoje:%Y-%m-%d}#extrato"
        r = self.client.post(url, {"data": hoje.isoformat(), "descricao": "Mercado", "valor": "32.40"})
        self.assertRedirects(r, painel, fetch_redirect_response=False)
        self.client.post(url, {"data": hoje.isoformat(), "descricao": "", "valor": "8"})
        tela = self.client.get(painel.split("#")[0])
        self.assertEqual(tela.context["dia"].gasto, Decimal("40.40"))
        self.assertContains(tela, "Mercado")
        g = GastoDia.objects.get(descricao="Mercado")
        self.client.post(reverse("financas:gasto_dia_excluir", args=[g.pk]))
        self.assertEqual(GastoDia.objects.count(), 1)

    def test_validacoes_e_outra_familia(self):
        url = reverse("financas:gasto_dia_adicionar", args=[self.conta.pk])
        self.client.post(url, {"data": "2099-01-01", "valor": "5"})
        self.client.post(url, {"data": "2026-10-31", "valor": "5"})      # antes do início
        self.client.post(url, {"data": "2026-11-02", "valor": "0"})
        self.assertFalse(GastoDia.objects.exists())
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        g = GastoDia.objects.create(conta=outra, data=date(2026, 1, 2), valor=1)
        self.assertEqual(self.client.post(reverse("financas:gasto_dia_adicionar", args=[outra.pk]),
                                          {"data": "2026-01-02", "valor": "1"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("financas:gasto_dia_excluir", args=[g.pk])).status_code, 404)


class AjusteDoMesTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.salario = ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                                tipo=ContaFixa.RECEITA, salario=True)
        self.url = reverse("financas:pagamento_registrar", args=[self.salario.pk])

    def test_muda_so_o_mes_escolhido(self):
        r = self.client.post(self.url, {"referencia": "2026-02-05", "valor": "4600", "data": "2026-02-04",
                                        "voltar": "dia:2026-02-05"})
        self.assertRedirects(r, reverse("financas:painel", args=[self.conta.pk]) + "?mes=2026-02&dia=2026-02-05#extrato",
                             fetch_redirect_response=False)
        fixa = ContaFixa.objects.get(pk=self.salario.pk)
        valores = [(e.data, e.valor, e.ajustado) for e in eventos_fixa(fixa, date(2026, 1, 1), date(2026, 3, 31))]
        self.assertEqual(valores, [(date(2026, 1, 5), Decimal("4000"), False),
                                   (date(2026, 2, 4), Decimal("4600"), True),
                                   (date(2026, 3, 5), Decimal("4000"), False)])
        self.assertEqual(fixa.valor, Decimal("4000"))                       # o cadastro não muda
        self.assertEqual(saldo_esperado(self.conta, date(2026, 3, 31)).esperado, Decimal("1000") + 4000 + 4600 + 4000)
        for d in extrato_mes(self.conta, 2026, 2):
            self.assertEqual(d.esperado, saldo_esperado(self.conta, d.data).esperado, d.data)

    def test_ajuste_futuro_continua_nos_proximos_e_desfaz(self):
        self.client.post(self.url, {"referencia": "2026-03-05", "valor": "4100", "data": "2026-03-05"})
        prox = proximas_fixas(self.conta, date(2026, 2, 10), 2)
        self.assertEqual([(d, e.valor) for d, e in prox], [(date(2026, 3, 5), Decimal("4100")), (date(2026, 4, 5), Decimal("4000"))])
        p = PagamentoConta.objects.get()
        self.client.post(reverse("financas:pagamento_excluir", args=[p.pk]))
        self.assertFalse(PagamentoConta.objects.exists())

    def test_tela_mostra_alterar_so_neste_mes(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]) + "?mes=2026-02&dia=2026-02-05")
        self.assertContains(r, "Alterar só neste mês")


class EncerrarTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("10000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("0"))
        self.aluguel = ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10)
        self.cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        self.netflix = AssinaturaCartao.objects.create(cartao=self.cartao, descricao="Netflix", valor=Decimal("40"),
                                                       dia_cobranca=8, inicio=date(2026, 1, 1))
        self.painel = reverse("financas:painel", args=[self.conta.pk])

    def test_encerrar_conta_fixa_mantem_o_passado(self):
        self.netflix.delete()   # isola o efeito do aluguel
        antes = saldo_esperado(self.conta, date(2026, 3, 31)).esperado
        r = self.client.post(reverse("financas:fixa_encerrar", args=[self.aluguel.pk]), {"ate": "2026-03-31"})
        self.assertRedirects(r, self.painel + "#contas", fetch_redirect_response=False)
        self.aluguel.refresh_from_db()
        self.assertEqual(self.aluguel.vigente_ate, date(2026, 3, 31))
        self.assertEqual(saldo_esperado(self.conta, date(2026, 3, 31)).esperado, antes)       # passado intacto
        self.assertEqual(saldo_esperado(self.conta, date(2026, 6, 30)).esperado, antes)       # abril em diante: sem aluguel
        self.client.post(reverse("financas:fixa_encerrar", args=[self.aluguel.pk]), {"reabrir": "1"})
        self.aluguel.refresh_from_db()
        self.assertIsNone(self.aluguel.vigente_ate)

    def test_fim_antes_do_inicio_e_sem_data(self):
        self.aluguel.vigente_desde = date(2026, 5, 1)
        self.aluguel.save()
        url = reverse("financas:fixa_encerrar", args=[self.aluguel.pk])
        self.client.post(url, {"ate": "2026-04-01"})
        self.client.post(url, {"ate": ""})
        self.aluguel.refresh_from_db()
        self.assertIsNone(self.aluguel.vigente_ate)

    def test_encerrar_e_reativar_assinatura(self):
        url = reverse("financas:assinatura_encerrar", args=[self.netflix.pk])
        self.client.post(url, {"fim": "2026-04-01"})
        self.netflix.refresh_from_db()
        self.assertEqual(self.netflix.fim, date(2026, 4, 1))
        self.client.post(url, {"fim": "2025-06-01"})          # antes do início: recusado
        self.netflix.refresh_from_db()
        self.assertEqual(self.netflix.fim, date(2026, 4, 1))
        self.client.post(url, {"reabrir": "1"})
        self.netflix.refresh_from_db()
        self.assertIsNone(self.netflix.fim)

    def test_botoes_na_lista_e_outra_familia(self):
        r = self.client.get(self.painel)
        self.assertContains(r, reverse("financas:fixa_encerrar", args=[self.aluguel.pk]))
        self.assertContains(r, reverse("financas:assinatura_encerrar", args=[self.netflix.pk]))
        self.assertContains(r, "Encerrar")
        self.client.post(reverse("financas:fixa_encerrar", args=[self.aluguel.pk]), {"ate": "2099-01-01"})
        self.assertContains(self.client.get(self.painel), "Reabrir")
        outra = Conta.objects.create(familia=Familia.objects.create(nome="Vizinhos"), nome="Deles",
                                     saldo_inicial=0, data_inicio=date(2026, 1, 1))
        alheia = ContaFixa.objects.create(conta=outra, nome="X", valor=1, dia=1)
        r = self.client.post(reverse("financas:fixa_encerrar", args=[alheia.pk]), {"ate": "2026-02-01"})
        self.assertEqual(r.status_code, 404)

    def test_formulario_ate_quando(self):
        url = reverse("financas:item_editar", args=["fixa", self.aluguel.pk])
        dados = campos(self.client.get(url), "form-fixa")
        self.assertEqual(dados["tem_fim"], "nao")
        dados.update({"tem_fim": "sim", "vigente_ate": "2026-08-31", "dia": "10"})
        self.client.post(url, dados)
        self.aluguel.refresh_from_db()
        self.assertEqual(self.aluguel.vigente_ate, date(2026, 8, 31))


class RecebidoMenosGastoTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        # janeiro/2026: 310/mês de dia a dia (10/dia), guardar 500 fixo
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"),
                                          guardar_modo=Conta.GUARDAR_FIXO, guardar_valor=Decimal("500"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=10)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 20), descricao="Freela", valor=Decimal("300"),
                                  tipo=Lancamento.ENTRADA)
        Lancamento.objects.create(conta=self.conta, data=date(2026, 1, 21), descricao="Presente", valor=Decimal("120"))
        cartao = Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=3, dia_vencimento=10)
        CompraCartao.objects.create(cartao=cartao, descricao="TV", valor_total=Decimal("200"), parcelas=1,
                                    data_compra=date(2026, 1, 1))   # fatura de jan, vence 10/01

    def test_totais_e_consistencia(self):
        conta = Conta.objects.get(pk=self.conta.pk)
        t = totais_do_mes(extrato_mes(conta, 2026, 1))
        self.assertEqual(t["recebido"], Decimal("4300"))                      # salário + freela
        self.assertEqual(t["guardado"], Decimal("500"))
        self.assertEqual(t["dia_a_dia"], Decimal("310"))
        self.assertEqual(t["gasto"], Decimal("1500") + 120 + 200 + 310)      # aluguel + presente + fatura + dia a dia
        self.assertEqual(t["resultado"], Decimal("4300") - Decimal("2130"))
        self.assertEqual(t["sobra"], Decimal("4300") - Decimal("2130") - Decimal("500"))   # menos o investido
        # o que sobrou menos o que foi guardado = quanto o saldo da conta variou no mês
        self.assertEqual(t["sobra"], t["fim"] - conta.saldo_inicial)

    def test_painel_mostra_mes_e_historico(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]) + "?mes=2026-01")
        self.assertEqual(r.context["totais_mes"]["sobra"], Decimal("1670"))
        self.assertContains(r, "Sobra do mês")
        self.assertContains(r, "− Investido")
        historico = r.context["historico_meses"]
        meses = [m["mes"] for m in historico]
        self.assertEqual(meses, sorted(meses))
        self.assertTrue(any(m["atual"] for m in historico))
        self.assertTrue(all(m["previsao"] for m in historico if m["mes"] > date.today().replace(day=1)))
        self.assertEqual(len([m for m in historico if m["previsao"]]), 2)


class ReservaPorMesTests(TestCase):
    """Reserva em % do salário acompanha o salário de cada mês."""

    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        hoje = date.today()
        self.mes_atual = hoje.replace(day=1)
        ano, mes = divmod(hoje.year * 12 + hoje.month, 12)   # mês seguinte
        self.mes_queda = date(ano, mes + 1, 1)
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("500"),
                                          data_inicio=self.mes_atual, gasto_mensal=Decimal("0"),
                                          reserva_modo=Conta.RESERVA_PERCENTUAL, reserva_percentual=Decimal("15"))
        # recebe 7000 até o fim deste mês; 1500 a partir do próximo (salário cai no último dia)
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("7000"), dia=31, tipo=ContaFixa.RECEITA,
                                 salario=True, vigente_ate=self.mes_queda - timedelta(days=1))
        ContaFixa.objects.create(conta=self.conta, nome="Salário menor", valor=Decimal("1500"), dia=31,
                                 tipo=ContaFixa.RECEITA, salario=True, vigente_desde=self.mes_queda)

    def nova(self):
        return Conta.objects.get(pk=self.conta.pk)

    def test_reserva_de_cada_mes(self):
        conta = self.nova()
        self.assertEqual(conta.reserva_em(self.mes_atual), Decimal("1050"))
        self.assertEqual(conta.reserva_em(self.mes_queda), Decimal("225"))
        self.assertEqual(conta.reserva, Decimal("1050"))

    def test_mesmo_saldo_cor_diferente_por_mes(self):
        conta = self.nova()
        este = extrato_mes(conta, self.mes_atual.year, self.mes_atual.month)
        aplicar_escala(este, conta)
        # 500 de saldo: abaixo da reserva de 1050 (entre ¼ e ½ → laranja) neste mês
        self.assertEqual(este[0].cor, "n2")
        legenda = aplicar_escala(este, conta)
        self.assertEqual(legenda["reserva"], Decimal("1050"))
        prox = extrato_mes(self.nova(), self.mes_queda.year, self.mes_queda.month)
        legenda_prox = aplicar_escala(prox, self.nova())
        self.assertEqual(legenda_prox["reserva"], Decimal("225"))
        dia1 = prox[0]
        self.assertEqual(dia1.cor, nivel_cor(dia1.esperado, Decimal("225"), Decimal("1500")))

    def test_alerta_e_projecao_usam_a_reserva_do_mes(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertEqual(r.context["alerta"]["reserva"], Decimal("1050"))
        self.assertEqual(r.context["legenda"]["reserva"], Decimal("1050"))
        Cartao.objects.create(conta=self.conta, nome="Roxinho", dia_fechamento=25, dia_vencimento=5)
        proj = self.client.get(reverse("financas:compra_nova", args=[self.conta.pk])).context["projecao"]
        self.assertEqual(proj["reservas"][f"{self.mes_atual:%Y-%m}"], 105000)
        self.assertEqual(proj["reservas"][f"{self.mes_queda:%Y-%m}"], 22500)
        self.assertEqual(proj["salarios"][f"{self.mes_queda:%Y-%m}"], 150000)

    def test_seletor_de_meses(self):
        anos = meses_do_seletor(self.nova(), self.mes_atual.year, self.mes_queda.year, date.today())
        meses = {m["data"]: m for a in anos for m in a["meses"]}
        m = meses[self.mes_queda]
        self.assertEqual(m["cor"], nivel_cor(m["fim"], Decimal("225"), Decimal("1500")))


class ExtratoDiarioMensalTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date(2026, 1, 1), gasto_mensal=Decimal("310"))
        ContaFixa.objects.create(conta=self.conta, nome="Salário", valor=Decimal("4000"), dia=5,
                                 tipo=ContaFixa.RECEITA, salario=True)
        ContaFixa.objects.create(conta=self.conta, nome="Aluguel", valor=Decimal("1500"), dia=5)
        self.url = reverse("financas:painel", args=[self.conta.pk])

    def test_abas_diario_e_mensal(self):
        r = self.client.get(self.url + "?mes=2026-01&dia=2026-01-05")
        self.assertContains(r, 'id="extrato"')
        self.assertContains(r, 'id="mensal"')
        self.assertContains(r, 'aria-controls="mensal"')
        self.assertContains(r, 'href="?mes=2026-02#mensal"')            # tabela navega na aba mensal
        self.assertContains(r, 'id="escolher-dia"')

    def test_dia_mostra_entradas_e_saidas(self):
        r = self.client.get(self.url + "?mes=2026-01&dia=2026-01-05")
        dia = r.context["dia"]
        self.assertEqual((dia.entradas, dia.total_saidas), (Decimal("4000"), Decimal("1510")))   # aluguel + 10 do dia a dia
        self.assertContains(r, "Entradas")
        self.assertContains(r, "Saídas")
        self.assertContains(r, "+ R$ 4.000,00")
        self.assertContains(r, "− R$ 1.510,00")


class MeusDadosTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.usuario = User.objects.get(username="arthur")
        self.url = reverse("financas:meus_dados")

    def test_cabecalho_leva_para_meus_dados(self):
        r = self.client.get(reverse("financas:familia"))
        self.assertContains(r, f'href="{self.url}"')

    def test_formulario_vem_preenchido_e_salva(self):
        r = self.client.get(self.url)
        dados = campos(r, "dados")
        self.assertEqual(dados["dados-first_name"], "Arthur")
        self.assertEqual(dados["dados-username"], "arthur")
        dados.update({"dados-last_name": "Silva", "dados-email": "arthur@exemplo.com",
                      "dados-parentesco": "pai"})
        self.assertRedirects(self.client.post(self.url, dados), self.url)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.get_full_name(), "Arthur Silva")
        self.assertEqual(self.usuario.email, "arthur@exemplo.com")
        self.assertEqual(self.usuario.membro.parentesco, "pai")

    def test_login_em_uso_nao_salva(self):
        cadastrar(self.client_class(), username="maria", familia=None)
        dados = dict(campos(self.client.get(self.url), "dados"), **{"dados-username": "Maria"})
        r = self.client.post(self.url, dados)
        self.assertContains(r, "Esse login já está em uso.")
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.username, "arthur")

    def test_trocar_senha_mantem_logado(self):
        r = self.client.post(self.url, {"acao": "senha", "senha-old_password": "senha-forte-123",
                                        "senha-new_password1": "outra-senha-456",
                                        "senha-new_password2": "outra-senha-456"})
        self.assertRedirects(r, self.url)
        self.assertEqual(self.client.get(self.url).status_code, 200)  # sessão continua válida
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password("outra-senha-456"))

    def test_senha_atual_errada(self):
        r = self.client.post(self.url, {"acao": "senha", "senha-old_password": "errada",
                                        "senha-new_password1": "outra-senha-456",
                                        "senha-new_password2": "outra-senha-456"})
        self.assertEqual(r.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password("senha-forte-123"))

    def test_sem_familia_tambem_acessa(self):
        outro = self.client_class()
        cadastrar(outro, username="joana", familia=None)
        r = outro.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("dados-parentesco", campos(r, "dados"))
