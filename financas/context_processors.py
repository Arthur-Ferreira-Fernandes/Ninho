from django.conf import settings


def marca(request):
    return {"marca": settings.MARCA}


TEMAS = ("escuro", "claro")


def tema(request):
    """Tema da interface: escuro por padrão; o botão do topo grava a escolha num cookie."""
    escolhido = request.COOKIES.get("tema")
    return {"tema": escolhido if escolhido in TEMAS else "escuro"}
