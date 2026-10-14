from django.contrib import admin

from .models import Aporte, Categoria, GastoDia, AssinaturaCartao, PagamentoConta, Cartao, CompraCartao, Conferencia, Conta, ContaFixa, Familia, Lancamento, Membro, Objetivo, PedidoEntrada, UsoDiario


class MembroInline(admin.TabularInline):
    model = Membro
    extra = 0


@admin.register(Familia)
class FamiliaAdmin(admin.ModelAdmin):
    list_display = ["nome", "codigo_convite", "criada_em"]
    inlines = [MembroInline]


@admin.register(PedidoEntrada)
class PedidoEntradaAdmin(admin.ModelAdmin):
    list_display = ["usuario", "familia", "parentesco", "criado_em"]


class ContaFixaInline(admin.TabularInline):
    model = ContaFixa
    extra = 0


@admin.register(Conta)
class ContaAdmin(admin.ModelAdmin):
    list_display = ["nome", "familia", "saldo_inicial", "data_inicio", "modo_gasto", "orcamento_mensal"]
    inlines = [ContaFixaInline]


@admin.register(Lancamento)
class LancamentoAdmin(admin.ModelAdmin):
    list_display = ["data", "descricao", "tipo", "valor", "categoria", "conta"]
    list_filter = ["tipo", "conta"]


@admin.register(Conferencia)
class ConferenciaAdmin(admin.ModelAdmin):
    list_display = ["data", "saldo_real", "conta"]


@admin.register(UsoDiario)
class UsoDiarioAdmin(admin.ModelAdmin):
    list_display = ["data", "situacao", "valor", "conta"]
    list_filter = ["situacao", "conta"]


class CompraCartaoInline(admin.TabularInline):
    model = CompraCartao
    extra = 0


@admin.register(Cartao)
class CartaoAdmin(admin.ModelAdmin):
    list_display = ["nome", "conta", "dia_fechamento", "dia_vencimento"]
    inlines = [CompraCartaoInline]


@admin.register(AssinaturaCartao)
class AssinaturaCartaoAdmin(admin.ModelAdmin):
    list_display = ["descricao", "cartao", "valor", "dia_cobranca", "inicio", "fim"]


@admin.register(PagamentoConta)
class PagamentoContaAdmin(admin.ModelAdmin):
    list_display = ["fixa", "referencia", "valor", "data"]


@admin.register(Aporte)
class AporteAdmin(admin.ModelAdmin):
    list_display = ["conta", "tipo", "referencia", "data", "valor", "descricao", "objetivo"]
    list_filter = ["tipo"]


@admin.register(GastoDia)
class GastoDiaAdmin(admin.ModelAdmin):
    list_display = ["data", "descricao", "valor", "categoria", "conta"]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ["nome", "familia", "limite_mensal", "ordem"]
    list_filter = ["familia"]


@admin.register(Objetivo)
class ObjetivoAdmin(admin.ModelAdmin):
    list_display = ["nome", "conta", "valor_alvo", "valor_inicial", "prazo", "padrao"]
