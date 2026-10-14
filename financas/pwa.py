"""App instalável (PWA) e verificação de saúde para a hospedagem."""
from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def saude(request):
    """A plataforma de hospedagem consulta aqui para saber se o app está no ar (inclui o banco)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - qualquer falha do banco é "fora do ar"
        return HttpResponse("banco indisponível", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


@require_GET
@cache_control(max_age=3600)
def manifest(request):
    marca = settings.MARCA
    return JsonResponse({
        "name": marca["nome"],
        "short_name": marca["nome"],
        "description": marca["slogan"],
        "lang": "pt-BR",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0e1413",
        "theme_color": "#0f5e55",
        "icons": [
            {"src": static("financas/img/icone-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("financas/img/icone-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("financas/img/icone-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")


@require_GET
@never_cache
def service_worker(request):
    # servido na raiz para valer para o site inteiro; a versão muda quando o CSS muda
    arquivos = [static("financas/css/ninho.css"), static("financas/img/logo.svg"),
                static("financas/img/icone-192.png")]
    resposta = render(request, "financas/pwa/sw.js", {"arquivos": arquivos, "versao": arquivos[0],
                                                       "prefixo_static": settings.STATIC_URL},
                      content_type="application/javascript")
    resposta["Service-Worker-Allowed"] = "/"
    return resposta


@require_GET
def offline(request):
    return render(request, "financas/pwa/offline.html")
