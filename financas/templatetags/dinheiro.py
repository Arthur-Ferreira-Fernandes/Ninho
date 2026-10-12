from django import template
from django.template.defaultfilters import floatformat

register = template.Library()


@register.filter
def brl(valor):
    """1234.5 -> 'R$ 1.234,50'; -10 -> '-R$ 10,00'."""
    if valor is None or valor == "":  # variável ausente no template
        return ""
    sinal = "-" if valor < 0 else ""
    return f"{sinal}R$ {floatformat(abs(valor), '2g')}"


@register.filter
def sub(valor, outro):
    """Subtração para templates: {{ a|sub:b }}."""
    return valor - outro
