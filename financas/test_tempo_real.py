from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Conta, Familia
from .tests import CADASTRO, cadastrar


class VersaoTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.familia = User.objects.get(username="arthur").membro.familia
        self.conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=Decimal("1000"),
                                          data_inicio=date.today(), gasto_mensal=Decimal("600"))

    def versao(self):
        return self.client.get(reverse("financas:versao")).json()["versao"]

    def test_alteracao_avanca_a_versao(self):
        antes = self.versao()
        r = self.client.post(reverse("financas:gasto_dia_adicionar", args=[self.conta.pk]),
                             {"data": date.today().isoformat(), "descricao": "Pão", "valor": "8"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.versao(), antes + 1)

    def test_o_que_nao_avanca(self):
        antes = self.versao()
        self.client.get(reverse("financas:painel", args=[self.conta.pk]))                   # só olhar
        r = self.client.post(reverse("financas:lancamento_novo", args=[self.conta.pk]), {})   # formulário com erro
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.versao(), antes)

    def test_outra_familia_nao_e_afetada(self):
        outra = Familia.objects.create(nome="Vizinhos")
        self.client.post(reverse("financas:gasto_dia_adicionar", args=[self.conta.pk]),
                         {"data": date.today().isoformat(), "descricao": "Pão", "valor": "8"})
        outra.refresh_from_db()
        self.assertEqual(outra.versao, 0)

    def test_entrar_e_sair_nao_avancam(self):
        antes = self.versao()
        self.client.post(reverse("financas:sair"))
        self.client.post(reverse("financas:entrar"), {"username": "arthur", "password": CADASTRO["password1"]})
        self.assertEqual(self.versao(), antes)

    def test_pagina_traz_a_versao_e_o_script(self):
        r = self.client.get(reverse("financas:painel", args=[self.conta.pk]))
        self.assertContains(r, f'<meta name="ninho-versao" content="{self.familia.versao}">')
        self.assertContains(r, "tempo-real")

    def test_sem_sessao(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("financas:versao")).status_code, 401)
        self.assertNotContains(self.client.get(reverse("financas:entrar")), "ninho-versao")


class NavegacaoCelularTests(TestCase):
    def setUp(self):
        cadastrar(self.client)
        self.familia = User.objects.get(username="arthur").membro.familia

    def test_barra_e_acoes_rapidas(self):
        conta = Conta.objects.create(familia=self.familia, nome="Nubank", saldo_inicial=0, data_inicio=date.today())
        r = self.client.get(reverse("financas:categorias"))   # página sem conta no contexto: usa a da família
        self.assertContains(r, 'class="nav-inferior"')
        self.assertContains(r, f'href="{reverse("financas:analise", args=[conta.pk])}" data-nav="analise"')
        self.assertContains(r, 'id="acoes-rapidas"')
        self.assertContains(r, f'{reverse("financas:painel", args=[conta.pk])}?dia={date.today().isoformat()}#gasto')

    def test_sem_conta_nao_tem_barra(self):
        r = self.client.get(reverse("financas:meus_dados"))
        self.assertNotContains(r, 'class="nav-inferior"')
        self.assertContains(r, "sair-perfil")   # no celular, o Sair fica em Meus dados
