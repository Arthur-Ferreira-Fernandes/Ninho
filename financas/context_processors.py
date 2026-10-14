from django.conf import settings


def marca(request):
    return {"marca": settings.MARCA, "endereco_remoto": settings.ENDERECO_REMOTO}


TEMAS = ("escuro", "claro")


def tema(request):
    """Tema da interface: escuro por padrão; o botão do topo grava a escolha num cookie."""
    escolhido = request.COOKIES.get("tema")
    return {"tema": escolhido if escolhido in TEMAS else "escuro"}


def navegacao(request):
    """Conta usada pela barra de navegação do celular (a da página, ou a primeira da família)."""
    membro = getattr(request.user, "membro", None) if request.user.is_authenticated else None
    if membro is None:
        return {"conta_nav": None}
    return {"conta_nav": membro.familia.contas.order_by("pk").first()}
