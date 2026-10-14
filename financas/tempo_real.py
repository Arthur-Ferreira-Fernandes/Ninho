"""Telas que se atualizam sozinhas quando algo muda em outro aparelho.

Cada família tem um número de versão. Toda alteração bem-sucedida (um POST respondido com
redirecionamento, o padrão de todas as telas do app) avança esse número. As páginas abertas
consultam /versao/ de tempos em tempos (static/financas/js/tempo-real.js) e, quando o número
muda, trocam o conteúdo pelo atual, sem recarregar.
"""

from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import Familia

# entrar e sair não mudam os dados da família
NAO_ALTERAM = {"entrar", "sair", "cadastro"}


def familia_do_usuario(user):
    if not user.is_authenticated:
        return None
    membro = getattr(user, "membro", None)
    return membro.familia if membro else None


def avancar_versao(familia_id):
    Familia.objects.filter(pk=familia_id).update(versao=F("versao") + 1)


class VersaoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        if request.method == "POST" and 300 <= resposta.status_code < 400:
            rota = getattr(request, "resolver_match", None)
            if not (rota and rota.url_name in NAO_ALTERAM):
                familia = familia_do_usuario(request.user)
                if familia:
                    avancar_versao(familia.pk)
        return resposta


@require_GET
@never_cache
def versao(request):
    familia = familia_do_usuario(request.user)
    if familia is None:
        return JsonResponse({"erro": "sem sessão"}, status=401)
    numero = Familia.objects.filter(pk=familia.pk).values_list("versao", flat=True).first()
    return JsonResponse({"versao": numero})


def contexto(request):
    """A versão com que a página foi montada (no <meta> da base)."""
    familia = familia_do_usuario(request.user)
    return {"versao_dados": familia.versao if familia else None}
