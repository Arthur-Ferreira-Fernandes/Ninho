import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import tailscale
from .tests import cadastrar

STATUS = json.dumps({"BackendState": "Running", "Self": {"DNSName": "casa.tail1234.ts.net."}})
SERVE = json.dumps({"Web": {"casa.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8003"}}}}})


def resposta(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


class TailscaleTests(SimpleTestCase):
    def test_nome_do_computador(self):
        self.assertEqual(tailscale.nome_do_computador(STATUS), "casa.tail1234.ts.net")
        parado = json.dumps({"BackendState": "Stopped", "Self": {"DNSName": "casa.tail1234.ts.net."}})
        self.assertIsNone(tailscale.nome_do_computador(parado))
        self.assertIsNone(tailscale.nome_do_computador("não é json"))

    def test_publicando(self):
        self.assertTrue(tailscale.publicando(SERVE, 8003))
        self.assertFalse(tailscale.publicando(SERVE, 8004))
        self.assertFalse(tailscale.publicando("{}", 8003))

    def detectar(self, *respostas):
        with mock.patch.object(tailscale.shutil, "which", return_value="/usr/bin/tailscale"), \
             mock.patch.object(tailscale, "_rodar", side_effect=list(respostas)) as rodar:
            return tailscale.detectar(8003), rodar

    def test_sem_tailscale(self):
        with mock.patch.object(tailscale.shutil, "which", return_value=None):
            self.assertIsNone(tailscale.detectar(8003))

    def test_ja_publicado(self):
        r, rodar = self.detectar(resposta(STATUS), resposta(SERVE))
        self.assertEqual(r, {"host": "casa.tail1234.ts.net", "publicado": True, "erro": ""})
        self.assertEqual(rodar.call_count, 2)   # não publica de novo

    def test_publica_quando_falta(self):
        r, rodar = self.detectar(resposta(STATUS), resposta("{}"), resposta())
        self.assertTrue(r["publicado"])
        self.assertEqual(rodar.call_args.args, ("serve", "--bg", "8003"))

    def test_https_nao_liberado(self):
        r, _ = self.detectar(resposta(STATUS), resposta("{}"), subprocess.TimeoutExpired("tailscale", 20))
        self.assertEqual((r["host"], r["publicado"]), ("casa.tail1234.ts.net", False))
        self.assertIn("HTTPS", r["erro"])
        r, _ = self.detectar(resposta(STATUS), resposta("{}"), resposta(returncode=1, stderr="access denied"))
        self.assertEqual(r["erro"], "access denied")

    def test_desconectado(self):
        r, _ = self.detectar(resposta(json.dumps({"BackendState": "NeedsLogin"})))
        self.assertIsNone(r)


class InstalarTelasTests(TestCase):
    def setUp(self):
        cadastrar(self.client)

    def test_passo_a_passo_em_todas_as_paginas(self):
        r = self.client.get(reverse("financas:meus_dados"))
        self.assertContains(r, 'id="como-instalar"')
        for caso in ("inseguro", "chrome", "android", "ios", "safari", "firefox"):
            self.assertContains(r, f'data-caso="{caso}"')
        self.assertContains(r, "desktop/acesso_remoto.sh")   # sem Tailscale: como configurar

    @override_settings(ENDERECO_REMOTO="https://casa.tail1234.ts.net/")
    def test_endereco_para_outros_aparelhos(self):
        r = self.client.get(reverse("financas:meus_dados"))
        self.assertContains(r, 'data-copiar="https://casa.tail1234.ts.net/"', count=2)   # cartão e janela

    def test_login_nao_tem_o_dialogo(self):
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("financas:entrar")), 'id="como-instalar"')
