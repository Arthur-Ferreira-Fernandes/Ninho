from django.conf import settings


def marca(request):
    return {"marca": settings.MARCA}
