from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

app_name = "financas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("entrar/", auth_views.LoginView.as_view(template_name="financas/entrar.html",
                                                 authentication_form=LoginForm,
                                                 redirect_authenticated_user=True), name="entrar"),
    path("sair/", auth_views.LogoutView.as_view(), name="sair"),
    path("cadastro/", views.cadastro, name="cadastro"),
    path("familia/", views.familia, name="familia"),
    path("familia/comecar/", views.sem_familia, name="sem_familia"),
    path("familia/pedido/cancelar/", views.pedido_cancelar, name="pedido_cancelar"),
    path("familia/codigo/novo/", views.codigo_novo, name="codigo_novo"),
    path("familia/pedidos/<int:pedido_pk>/aceitar/", views.pedido_responder, {"aceitar": True}, name="pedido_aceitar"),
    path("familia/pedidos/<int:pedido_pk>/recusar/", views.pedido_responder, {"aceitar": False}, name="pedido_recusar"),
    path("familia/pessoas/<int:membro_pk>/admin/", views.membro_alternar_admin, name="membro_admin"),
    path("familia/pessoas/<int:membro_pk>/remover/", views.membro_remover, name="membro_remover"),
    path("contas/nova/", views.conta_nova, name="conta_nova"),
    path("contas/<int:pk>/", views.painel, name="painel"),
    path("contas/<int:pk>/editar/", views.conta_editar, name="conta_editar"),
    path("contas/<int:pk>/uso-diario/", views.uso_diario, name="uso_diario"),
    path("contas/<int:pk>/gastos-do-dia/", views.gasto_dia_adicionar, name="gasto_dia_adicionar"),
    path("gastos-do-dia/<int:gasto_pk>/excluir/", views.gasto_dia_excluir, name="gasto_dia_excluir"),
    path("fixas/<int:fixa_pk>/pagamento/", views.pagamento_registrar, name="pagamento_registrar"),
    path("fixas/<int:fixa_pk>/encerrar/", views.fixa_encerrar, name="fixa_encerrar"),
    path("assinaturas/<int:assinatura_pk>/encerrar/", views.assinatura_encerrar, name="assinatura_encerrar"),
    path("contas/<int:pk>/guardado/confirmar/", views.aporte_confirmar, name="aporte_confirmar"),
    path("contas/<int:pk>/guardado/avulso/", views.aporte_avulso, name="aporte_avulso"),
    path("guardado/<int:aporte_pk>/excluir/", views.aporte_excluir, name="aporte_excluir"),
    path("pagamentos/<int:pagamento_pk>/excluir/", views.pagamento_excluir, name="pagamento_excluir"),
    path("contas/<int:pk>/cartoes/novo/", views.cartao_form, name="cartao_novo"),
    path("cartoes/<int:cartao_pk>/editar/", views.cartao_form, name="cartao_editar"),
    path("cartoes/<int:cartao_pk>/excluir/", views.cartao_excluir, name="cartao_excluir"),
    path("contas/<int:pk>/compras/nova/", views.compra_form, name="compra_nova"),
    path("compras/<int:compra_pk>/editar/", views.compra_form, name="compra_editar"),
    path("compras/<int:compra_pk>/excluir/", views.compra_excluir, name="compra_excluir"),
    path("contas/<int:pk>/assinaturas/nova/", views.assinatura_form, name="assinatura_nova"),
    path("assinaturas/<int:assinatura_pk>/editar/", views.assinatura_form, name="assinatura_editar"),
    path("assinaturas/<int:assinatura_pk>/excluir/", views.assinatura_excluir, name="assinatura_excluir"),
    path("contas/<int:pk>/fixas/nova/", views.item_novo, {"modelo": "fixa"}, name="fixa_nova"),
    path("contas/<int:pk>/lancamentos/novo/", views.item_novo, {"modelo": "lancamento"}, name="lancamento_novo"),
    path("contas/<int:pk>/conferencias/nova/", views.item_novo, {"modelo": "conferencia"}, name="conferencia_nova"),
    path("<str:modelo>/<int:item_pk>/editar/", views.item_editar, name="item_editar"),
    path("<str:modelo>/<int:item_pk>/excluir/", views.item_excluir, name="item_excluir"),
    path("conferencias/<int:item_pk>/recomecar/", views.recomecar, name="recomecar"),
]
