import calendar
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import User

from .templatetags.dinheiro import brl
from .models import (Aporte, Categoria, GastoDia, AssinaturaCartao, Cartao, CompraCartao, PagamentoConta, Conferencia,
                     ConferenciaInvestimento, Conta, ContaFixa, Familia, Lancamento, Objetivo, UsoDiario)

DATA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _dinheiro(placeholder="0,00"):
    return forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal",
                                    "placeholder": placeholder})


class ComCategoria:
    """Campo "categoria" opcional: vazio, a categoria é sugerida pela descrição ao salvar."""

    campo_descricao = "descricao"

    def _preparar_categoria(self, familia):
        self.familia = familia
        campo = self.fields["categoria"]
        campo.required = False
        campo.label = "Categoria"
        campo.empty_label = "Automática (pela descrição)"
        campo.help_text = ""
        campo.queryset = familia.lista_categorias() if familia else Categoria.objects.none()

    def _completar_categoria(self, item):
        if item.categoria_id is None and getattr(self, "familia", None):
            item.categoria = self.familia.sugerir_categoria(getattr(item, self.campo_descricao))
        return item


class ContaForm(forms.ModelForm):
    class Meta:
        model = Conta
        fields = ["nome", "saldo_inicial", "data_inicio",
                  "modo_gasto", "percentual_gasto", "gasto_mensal",
                  "guardar_modo", "guardar_percentual", "guardar_valor",
                  "reserva_modo", "reserva_minima", "reserva_percentual"]
        labels = {
            "nome": "Nome da conta",
            "saldo_inicial": "Saldo atual",
            "data_inicio": "Começar a contar a partir de",
            "modo_gasto": "Como definir o gasto do dia a dia",
            "percentual_gasto": "Porcentagem do salário",
            "gasto_mensal": "Valor por mês",
            "guardar_modo": "Quanto guardar por mês",
            "guardar_percentual": "Porcentagem do salário",
            "guardar_valor": "Valor por mês",
            "reserva_modo": "Como definir a reserva",
            "reserva_minima": "Valor da reserva",
            "reserva_percentual": "Porcentagem do salário",
        }
        help_texts = {
            "saldo_inicial": "Quanto tem na conta agora, no início desse dia.",
            "data_inicio": "Normalmente hoje.",
            "percentual_gasto": "",
            "gasto_mensal": "",
        }
        widgets = {
            "nome": forms.TextInput(attrs={"placeholder": "Ex.: Nubank, Conta conjunta", "autofocus": True}),
            "saldo_inicial": forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0,00"}),
            "data_inicio": DATA,
            "modo_gasto": forms.RadioSelect,
            "percentual_gasto": forms.NumberInput(attrs={"step": "0.5", "min": "0", "max": "100",
                                                         "inputmode": "decimal", "placeholder": "Ex.: 30"}),
            "gasto_mensal": _dinheiro("Ex.: 1.500,00"),
            "guardar_modo": forms.RadioSelect,
            "guardar_percentual": forms.NumberInput(attrs={"step": "0.5", "min": "0", "max": "100",
                                                           "inputmode": "decimal", "placeholder": "Ex.: 10"}),
            "guardar_valor": _dinheiro("Ex.: 500,00"),
            "reserva_modo": forms.RadioSelect,
            "reserva_minima": _dinheiro("Ex.: 800,00"),
            "reserva_percentual": forms.NumberInput(attrs={"step": "0.5", "min": "0", "max": "100",
                                                           "inputmode": "decimal", "placeholder": "Ex.: 15"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gasto_mensal"].required = False
        self.fields["guardar_modo"].required = False
        self.fields["reserva_modo"].required = False


    def clean(self):
        dados = super().clean()
        modo = dados.get("modo_gasto")
        if modo == Conta.PERCENTUAL:
            pct = dados.get("percentual_gasto")
            if pct is None:
                self.add_error("percentual_gasto", "Informe a porcentagem.")
            elif not 0 < pct <= 100:
                self.add_error("percentual_gasto", "Use um valor entre 0 e 100.")
        elif modo == Conta.MENSAL:
            if not dados.get("gasto_mensal"):
                self.add_error("gasto_mensal", "Informe quanto pode gastar por mês.")
            dados["percentual_gasto"] = None

        guardar = dados.get("guardar_modo")
        if guardar == Conta.GUARDAR_PERCENTUAL:
            pct = dados.get("guardar_percentual")
            if pct is None:
                self.add_error("guardar_percentual", "Informe a porcentagem a guardar.")
            elif not 0 < pct <= 100:
                self.add_error("guardar_percentual", "Use um valor entre 0 e 100.")
            dados["guardar_valor"] = None
        elif guardar == Conta.GUARDAR_FIXO:
            valor = dados.get("guardar_valor")
            if not valor or valor <= 0:
                self.add_error("guardar_valor", "Informe quanto guardar por mês.")
            dados["guardar_percentual"] = None
        else:
            dados["guardar_modo"] = Conta.GUARDAR_NADA
            dados["guardar_percentual"] = dados["guardar_valor"] = None

        reserva = dados.get("reserva_modo")
        if reserva == Conta.RESERVA_FIXO:
            valor = dados.get("reserva_minima")
            if valor is None:
                self.add_error("reserva_minima", "Informe o valor da reserva.")
            elif valor < 0:
                self.add_error("reserva_minima", "A reserva não pode ser negativa.")
            dados["reserva_percentual"] = None
        elif reserva == Conta.RESERVA_PERCENTUAL:
            pct = dados.get("reserva_percentual")
            if pct is None:
                self.add_error("reserva_percentual", "Informe a porcentagem do salário.")
            elif not 0 < pct <= 100:
                self.add_error("reserva_percentual", "Use um valor entre 0 e 100.")
            dados["reserva_minima"] = None
        else:
            dados["reserva_modo"] = Conta.RESERVA_AUTO
            dados["reserva_minima"] = dados["reserva_percentual"] = None
        return dados


class PagamentoSalarioForm(forms.ModelForm):
    """Um pagamento do salário: o dia do mês em que cai, quanto cai e de quem/o que é."""

    class Meta:
        model = ContaFixa
        fields = ["dia", "valor", "descricao", "vigente_desde", "vigente_ate"]
        labels = {"dia": "Dia", "valor": "Valor", "descricao": "Descrição",
                  "vigente_desde": "A partir de", "vigente_ate": "Até"}
        help_texts = {"dia": "", "vigente_desde": "", "vigente_ate": ""}
        widgets = {
            "dia": forms.NumberInput(attrs={"min": "1", "max": "31", "inputmode": "numeric", "placeholder": "Dia"}),
            "valor": _dinheiro(),
            "descricao": forms.TextInput(attrs={"placeholder": "Descrição (opcional): ex.: Salário da Ana, Adiantamento",
                                                "autocomplete": "off", "maxlength": "60"}),
            "vigente_desde": DATA,
            "vigente_ate": DATA,
        }

    def clean(self):
        dados = super().clean()
        desde, ate = dados.get("vigente_desde"), dados.get("vigente_ate")
        if desde and ate and ate < desde:
            self.add_error("vigente_ate", "O “até” não pode ser antes do “a partir de”.")
        return dados

    def clean_dia(self):
        dia = self.cleaned_data["dia"]
        if not 1 <= dia <= 31:
            raise forms.ValidationError("Dia entre 1 e 31.")
        return dia

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Valor maior que zero.")
        return valor


class BasePagamentosSalario(forms.BaseInlineFormSet):
    def __init__(self, *args, exigir=False, **kwargs):
        # exigir: o modo percentual precisa de pelo menos um pagamento
        self.exigir = exigir
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(salario=True).order_by("dia")

    def pagamentos_validos(self):
        return [f for f in self.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")]

    def total(self):
        return sum((f.cleaned_data["valor"] for f in self.pagamentos_validos()), Decimal("0"))

    def clean(self):
        super().clean()
        if self.exigir and not self.pagamentos_validos():
            raise forms.ValidationError(
                "Informe pelo menos um pagamento do salário para calcular a porcentagem.")

    def save(self, commit=True):
        """Salva os pagamentos como receitas fixas marcadas como salário."""
        pagamentos = super().save(commit=False)
        for obj in self.deleted_objects:
            obj.delete()
        mantidos = [f.instance for f in self.pagamentos_validos()]
        for p in mantidos:
            p.conta = self.instance
            p.tipo = ContaFixa.RECEITA
            p.salario = True
            p.nome = _nome_do_pagamento(p, len(mantidos))
            p.save()
        return pagamentos


def _nome_do_pagamento(p, quantidade):
    """Nome exibido: a descrição (ou "Salário") mais o dia e o período quando ajudam a distinguir."""
    detalhes = [] if quantidade == 1 else [f"dia {p.dia}"]
    if p.vigente_desde:
        detalhes.append(f"a partir de {p.vigente_desde:%m/%Y}")
    if p.vigente_ate:
        detalhes.append(f"até {p.vigente_ate:%m/%Y}")
    return (p.descricao or "Salário") + (f" ({', '.join(detalhes)})" if detalhes else "")


PagamentosSalarioFormSet = forms.inlineformset_factory(
    Conta, ContaFixa, form=PagamentoSalarioForm, formset=BasePagamentosSalario,
    extra=0, can_delete=True,
)


class ContaFixaForm(ComCategoria, forms.ModelForm):
    campo_descricao = "nome"

    dia = forms.TypedChoiceField(
        label="Dia do mês", coerce=int, choices=[(d, d) for d in range(1, 32)], required=False,
        widget=forms.RadioSelect, empty_value=None,
        error_messages={"invalid_choice": "Escolha um dia entre 1 e 31."},
    )

    class Meta:
        model = ContaFixa
        fields = ["tipo", "nome", "variavel", "valor", "frequencia", "dia", "intervalo_dias", "proxima",
                  "vigente_desde", "vigente_ate", "ativa", "categoria"]
        labels = {"tipo": "Tipo", "nome": "Descrição", "valor": "Valor", "ativa": "Ativa",
                  "variavel": "O valor muda todo mês", "frequencia": "Com que frequência",
                  "intervalo_dias": "A cada quantos dias", "proxima": "Próxima vez",
                  "vigente_desde": "Vale a partir de", "vigente_ate": "Vale até"}
        help_texts = {"ativa": "Desligada, deixa de contar em todos os meses, inclusive nos que já passaram.",
                      "variavel": "Luz, água, mercado… O valor abaixo vira uma estimativa, e a cada mês "
                                  "você marca como paga informando o valor real."}
        widgets = {
            "tipo": forms.RadioSelect,
            "nome": forms.TextInput(attrs={"placeholder": "Ex.: Aluguel, Internet, Energia", "autocomplete": "off"}),
            "valor": _dinheiro(),
            "frequencia": forms.RadioSelect,
            "intervalo_dias": forms.NumberInput(attrs={"min": "1", "max": "3650", "inputmode": "numeric",
                                                       "placeholder": "Ex.: 45"}),
            "proxima": DATA,
            "vigente_desde": DATA,
            "vigente_ate": DATA,
        }

    def __init__(self, *args, familia=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._preparar_categoria(familia)
        self.fields["vigente_desde"].help_text = ""
        self.fields["vigente_ate"].label = "Último dia em que vale"
        self.fields["vigente_ate"].help_text = "Ex.: o aluguel termina quando você se mudar. Os meses anteriores continuam valendo."
        self.fields["vigente_desde"].label = "Começa em"
        if not self.instance.pk:
            del self.fields["ativa"]  # novas já nascem ativas
        if self.instance.salario:
            # o campo "Descrição" edita a descrição; o nome é montado ao salvar (como em Configurar conta)
            self.fields["nome"].required = False
            self.fields["nome"].max_length = 60
            self.fields["nome"].widget.attrs.update(maxlength="60", placeholder="Ex.: Salário da Ana, Adiantamento")
            self.initial["nome"] = self.instance.descricao
            del self.fields["tipo"]        # pagamento de salário é sempre receita
            del self.fields["categoria"]   # e receita não tem categoria
            del self.fields["variavel"]    # e tem valor definido na configuração da conta
            del self.fields["frequencia"]  # e cai todo mês
            del self.fields["intervalo_dias"]
            del self.fields["proxima"]
        else:
            self.fields["frequencia"].required = False

    def clean(self):
        dados = super().clean()
        desde, ate = dados.get("vigente_desde"), dados.get("vigente_ate")
        if desde and ate and ate < desde:
            self.add_error("vigente_ate", "O “vale até” não pode ser antes do “vale a partir de”.")
        if "frequencia" not in self.fields:   # salário: sempre mensal, só o dia importa
            if not dados.get("dia"):
                self.add_error("dia", "Escolha o dia do mês.")
            return dados
        if dados.get("frequencia") == ContaFixa.DIAS:
            intervalo, proxima = dados.get("intervalo_dias"), dados.get("proxima")
            if not intervalo:
                self.add_error("intervalo_dias", "Informe de quantos em quantos dias.")
            elif not 1 <= intervalo <= 3650:
                self.add_error("intervalo_dias", "Use de 1 a 3650 dias.")
            if not proxima:
                self.add_error("proxima", "Informe a data prevista da próxima vez.")
            else:
                dados["dia"] = proxima.day   # o dia do mês não se aplica; guarda só por referência
        else:
            dados["frequencia"] = ContaFixa.MENSAL
            if not dados.get("dia"):
                self.add_error("dia", "Escolha o dia do mês.")
            dados["intervalo_dias"] = dados["proxima"] = None
        return dados

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def save(self, commit=True):
        item = super().save(commit=False)
        if item.salario:
            item.descricao = self.cleaned_data.get("nome") or ""
            item.nome = _nome_do_pagamento(item, item.conta.fixas.filter(salario=True).count())
        if item.tipo == ContaFixa.RECEITA:
            item.categoria = None
        else:
            self._completar_categoria(item)
        if commit:
            item.save()
        return item


class LancamentoForm(ComCategoria, forms.ModelForm):
    class Meta:
        model = Lancamento
        fields = ["tipo", "descricao", "valor", "data", "categoria"]
        labels = {"tipo": "Tipo", "descricao": "Descrição", "valor": "Valor", "data": "Data"}
        widgets = {
            "tipo": forms.RadioSelect,
            "descricao": forms.TextInput(attrs={"placeholder": "Ex.: Conserto do carro, Freela", "autocomplete": "off"}),
            "valor": _dinheiro(),
            "data": DATA,
        }

    def __init__(self, *args, familia=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._preparar_categoria(familia)

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def save(self, commit=True):
        item = super().save(commit=False)
        if item.tipo == Lancamento.ENTRADA:
            item.categoria = None     # categorias são para o que sai
        else:
            self._completar_categoria(item)
        if commit:
            item.save()
        return item


class ConferenciaForm(forms.ModelForm):
    class Meta:
        model = Conferencia
        fields = ["data", "saldo_real"]
        widgets = {"data": DATA}


def _attrs(form, campos):
    for nome, attrs in campos.items():
        form.fields[nome].widget.attrs.update(attrs)


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Login"
        _attrs(self, {
            "username": {"placeholder": "seu.login", "autocomplete": "username", "autofocus": True},
            "password": {"placeholder": "Sua senha", "autocomplete": "current-password"},
        })


class UsuarioForm(UserCreationForm):
    """Cria um usuário com nome e e-mail, além de login e senha."""

    first_name = forms.CharField(label="Seu nome", max_length=150)
    email = forms.EmailField(label="E-mail", required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["first_name", "email", "username"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Login"
        self.fields["username"].help_text = "Você vai usar para entrar. Letras, números e @ . + - _"
        self.fields["password1"].help_text = "Pelo menos 8 caracteres, sem ser só números."
        self.fields["password2"].label = "Confirme a senha"
        self.fields["password2"].help_text = ""
        _attrs(self, {
            "first_name": {"placeholder": "Como podemos te chamar?", "autocomplete": "given-name", "autofocus": True},
            "email": {"placeholder": "voce@email.com", "autocomplete": "email"},
            "username": {"placeholder": "ex.: maria.silva", "autocomplete": "username"},
            "password1": {"placeholder": "Crie uma senha", "autocomplete": "new-password"},
            "password2": {"placeholder": "Repita a senha", "autocomplete": "new-password"},
        })


class PerfilForm(forms.ModelForm):
    """Dados pessoais do usuário logado; o parentesco fica no vínculo com a família."""

    first_name = forms.CharField(label="Nome", max_length=150)
    parentesco = forms.CharField(label="Parentesco na família", max_length=50, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "username"]
        labels = {"last_name": "Sobrenome", "email": "E-mail", "username": "Login"}

    def __init__(self, *args, membro=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.membro = membro
        if membro is None:
            del self.fields["parentesco"]
        else:
            self.fields["parentesco"].initial = membro.parentesco
        self.fields["username"].help_text = "Usado para entrar. Letras, números e @ . + - _"
        _attrs(self, {
            "first_name": {"placeholder": "Como podemos te chamar?", "autocomplete": "given-name"},
            "last_name": {"placeholder": "Seu sobrenome", "autocomplete": "family-name"},
            "email": {"placeholder": "voce@email.com", "autocomplete": "email"},
            "username": {"placeholder": "ex.: maria.silva", "autocomplete": "username"},
        })
        if membro is not None:
            _attrs(self, {"parentesco": {"placeholder": "Ex.: esposa, filho, mãe"}})

    def clean_username(self):
        login = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=login).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Esse login já está em uso.")
        return login

    def save(self, commit=True):
        usuario = super().save(commit)
        if commit and self.membro is not None:
            self.membro.parentesco = self.cleaned_data["parentesco"].strip()
            self.membro.save(update_fields=["parentesco"])
        return usuario


class SenhaForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Senha atual"
        self.fields["new_password1"].label = "Nova senha"
        self.fields["new_password1"].help_text = "Pelo menos 8 caracteres, sem ser só números."
        self.fields["new_password2"].label = "Confirme a nova senha"
        self.fields["new_password2"].help_text = ""
        _attrs(self, {
            "old_password": {"placeholder": "Sua senha de hoje", "autofocus": False},
            "new_password1": {"placeholder": "Crie uma nova senha"},
            "new_password2": {"placeholder": "Repita a nova senha"},
        })
        self.fields["old_password"].widget.attrs.pop("autofocus", None)


class EntrarFamiliaForm(forms.Form):
    codigo = forms.CharField(label="Código de convite", max_length=20, widget=forms.TextInput(
        attrs={"placeholder": "Ex.: K7MPQ3XA", "autocomplete": "off", "spellcheck": "false"}))
    parentesco = forms.CharField(label="Seu parentesco", max_length=50, required=False,
                                 widget=forms.TextInput(attrs={"placeholder": "Ex.: esposa, filho, mãe"}))

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip().upper().replace("-", "").replace(" ", "")
        try:
            self.familia = Familia.objects.get(codigo_convite=codigo)
        except Familia.DoesNotExist:
            raise forms.ValidationError("Código inválido. Confira com quem te convidou.")
        return codigo


class FamiliaForm(forms.ModelForm):
    class Meta:
        model = Familia
        fields = ["nome"]
        labels = {"nome": "Nome da família"}
        widgets = {"nome": forms.TextInput(attrs={"placeholder": "Ex.: Família Silva"})}


class UsoDiarioForm(forms.ModelForm):
    class Meta:
        model = UsoDiario
        fields = ["data", "situacao", "valor"]

    def __init__(self, *args, conta, hoje, **kwargs):
        self.conta, self.hoje = conta, hoje
        super().__init__(*args, **kwargs)

    def clean_data(self):
        data = self.cleaned_data["data"]
        if data < self.conta.data_inicio:
            raise forms.ValidationError("Esse dia é anterior ao início do cálculo.")
        if data > self.hoje:
            raise forms.ValidationError("Só dá para informar o uso de hoje ou de dias passados.")
        return data

    def clean(self):
        dados = super().clean()
        if dados.get("situacao") == UsoDiario.PARTE:
            valor = dados.get("valor")
            if valor is None:
                self.add_error("valor", "Informe quanto foi usado.")
            elif valor < 0:
                self.add_error("valor", "O valor não pode ser negativo.")
        else:
            dados["valor"] = None
        return dados


def _dia_do_mes(nome):
    return forms.IntegerField(
        label=nome, min_value=1, max_value=31,
        widget=forms.NumberInput(attrs={"min": "1", "max": "31", "inputmode": "numeric", "placeholder": "Dia"}),
        error_messages={"min_value": "Use um dia entre 1 e 31.", "max_value": "Use um dia entre 1 e 31."},
    )


class CartaoForm(forms.ModelForm):
    dia_fechamento = _dia_do_mes("Dia do fechamento")
    dia_vencimento = _dia_do_mes("Dia do vencimento")

    class Meta:
        model = Cartao
        fields = ["nome", "dia_fechamento", "dia_vencimento"]
        labels = {"nome": "Nome do cartão"}
        widgets = {"nome": forms.TextInput(attrs={"placeholder": "Ex.: Nubank, Itaú Visa", "autofocus": True})}


class CompraCartaoForm(ComCategoria, forms.ModelForm):
    parcelas = forms.IntegerField(
        label="Parcelas", min_value=1, max_value=48, initial=1,
        widget=forms.NumberInput(attrs={"min": "1", "max": "48", "inputmode": "numeric"}),
        error_messages={"min_value": "Pelo menos 1 parcela.", "max_value": "No máximo 48 parcelas."},
    )

    class Meta:
        model = CompraCartao
        fields = ["cartao", "descricao", "valor_total", "parcelas", "data_compra", "categoria"]
        labels = {"cartao": "Cartão", "descricao": "Descrição", "valor_total": "Valor total da compra",
                  "data_compra": "Data da compra"}
        widgets = {
            "cartao": forms.RadioSelect,
            "descricao": forms.TextInput(attrs={"placeholder": "Ex.: Geladeira, Passagens", "autocomplete": "off"}),
            "valor_total": _dinheiro(),
            "data_compra": DATA,
        }

    def __init__(self, *args, conta, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cartao"].queryset = conta.cartoes.all()
        self.fields["cartao"].empty_label = None
        self.fields["cartao"].error_messages["required"] = "Escolha o cartão."
        self._preparar_categoria(conta.familia)

    def clean_valor_total(self):
        valor = self.cleaned_data["valor_total"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def save(self, commit=True):
        item = self._completar_categoria(super().save(commit=False))
        if commit:
            item.save()
        return item


class AssinaturaForm(ComCategoria, forms.ModelForm):
    dia_cobranca = _dia_do_mes("Dia da cobrança")

    class Meta:
        model = AssinaturaCartao
        fields = ["cartao", "descricao", "valor", "dia_cobranca", "inicio", "fim", "categoria"]
        labels = {"cartao": "Cartão", "descricao": "Assinatura", "valor": "Valor mensal",
                  "inicio": "Cobra a partir de", "fim": "Cancelada a partir de"}
        help_texts = {"fim": "Deixe vazio enquanto a assinatura estiver ativa.", "inicio": ""}
        widgets = {
            "cartao": forms.RadioSelect,
            "descricao": forms.TextInput(attrs={"placeholder": "Ex.: Netflix, Spotify, Academia", "autocomplete": "off"}),
            "valor": _dinheiro(),
            "inicio": DATA,
            "fim": DATA,
        }

    def __init__(self, *args, conta, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cartao"].queryset = conta.cartoes.all()
        self.fields["cartao"].empty_label = None
        self.fields["cartao"].error_messages["required"] = "Escolha o cartão."
        self._preparar_categoria(conta.familia)

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if inicio and fim and fim < inicio:
            self.add_error("fim", "O cancelamento não pode ser antes do início.")
        return dados

    def save(self, commit=True):
        item = self._completar_categoria(super().save(commit=False))
        if commit:
            item.save()
        return item


class PagamentoContaForm(forms.ModelForm):
    class Meta:
        model = PagamentoConta
        fields = ["valor", "data"]

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor


class AporteForm(forms.ModelForm):
    """Valor guardado: confirmação do mês ou avulso."""

    class Meta:
        model = Aporte
        fields = ["data", "valor", "descricao", "objetivo"]

    def __init__(self, *args, conta, hoje, **kwargs):
        self.conta, self.hoje = conta, hoje
        super().__init__(*args, **kwargs)
        self.fields["descricao"].required = False
        self.fields["objetivo"].queryset = conta.objetivos.all()

    def save(self, commit=True):
        aporte = super().save(commit=False)
        if aporte.objetivo_id is None and aporte.valor > 0 and "objetivo" not in self.data:
            # sem o campo no formulário: vai para o objetivo que recebe o guardado do mês, se houver
            # ("Sem objetivo" escolhido envia o campo vazio e é respeitado)
            aporte.objetivo = self.conta.objetivos.filter(padrao=True).first()
        if commit:
            aporte.save()
        return aporte

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def clean_data(self):
        data = self.cleaned_data["data"]
        if data < self.conta.data_inicio:
            raise forms.ValidationError("A data é anterior ao início do cálculo da conta.")
        ref = self.instance.referencia
        if self.instance.tipo == Aporte.MENSAL and ref:
            if data > self.hoje:
                raise forms.ValidationError("Só dá para confirmar o que já foi guardado (data até hoje).")
            ano, mes = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
            limite = date(ano, mes, calendar.monthrange(ano, mes)[1])
            if not ref <= data <= limite:
                raise forms.ValidationError("Use uma data do próprio mês ou do mês seguinte.")
        return data


class GastoDiaForm(ComCategoria, forms.ModelForm):
    class Meta:
        model = GastoDia
        fields = ["data", "descricao", "valor", "categoria"]

    def __init__(self, *args, conta, hoje, **kwargs):
        self.conta, self.hoje = conta, hoje
        super().__init__(*args, **kwargs)
        self.fields["descricao"].required = False
        self._preparar_categoria(conta.familia)

    def save(self, commit=True):
        item = self._completar_categoria(super().save(commit=False))
        if commit:
            item.save()
        return item

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def clean_data(self):
        data = self.cleaned_data["data"]
        if data < self.conta.data_inicio:
            raise forms.ValidationError("Esse dia é anterior ao início do cálculo.")
        if data > self.hoje:
            raise forms.ValidationError("Só dá para lançar gastos de hoje ou de dias passados.")
        return data


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nome", "limite_mensal"]
        labels = {"nome": "Nome", "limite_mensal": "Limite por mês"}
        widgets = {
            "nome": forms.TextInput(attrs={"placeholder": "Ex.: Farmácia, Filhos, Carro", "autocomplete": "off",
                                           "maxlength": "40"}),
            "limite_mensal": _dinheiro("sem limite"),
        }

    def __init__(self, *args, familia, **kwargs):
        self.familia = familia
        super().__init__(*args, **kwargs)
        self.fields["limite_mensal"].help_text = ""

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        repetida = self.familia.categorias.filter(nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError("Já existe uma categoria com esse nome.")
        return nome

    def clean_limite_mensal(self):
        limite = self.cleaned_data["limite_mensal"]
        if limite is not None and limite <= 0:
            return None   # zero ou negativo = sem limite
        return limite


class ObjetivoForm(forms.ModelForm):
    class Meta:
        model = Objetivo
        fields = ["nome", "valor_alvo", "prazo", "valor_inicial", "padrao"]
        labels = {"nome": "Para quê", "valor_alvo": "Quanto quer juntar", "prazo": "Até quando",
                  "valor_inicial": "Quanto já tem guardado para isso", "padrao": "Recebe o guardado de todo mês"}
        help_texts = {"prazo": "Com prazo, o app mostra quanto guardar por mês para chegar a tempo.",
                      "valor_inicial": "Dinheiro que já estava guardado antes de usar o app.",
                      "padrao": "Ao confirmar o guardado do mês sem escolher outro objetivo, o valor vai para este."}
        widgets = {
            "nome": forms.TextInput(attrs={"placeholder": "Ex.: Reserva de emergência, Viagem, Carro novo",
                                           "autocomplete": "off"}),
            "valor_alvo": _dinheiro(),
            "prazo": DATA,
            "valor_inicial": _dinheiro(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor_inicial"].required = False   # vazio = nada guardado ainda

    def clean_valor_alvo(self):
        valor = self.cleaned_data["valor_alvo"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def clean_valor_inicial(self):
        valor = self.cleaned_data["valor_inicial"] or Decimal("0")
        if valor < 0:
            raise forms.ValidationError("O valor não pode ser negativo.")
        return valor


class CarteiraForm(forms.ModelForm):
    """Quanto já havia investido numa data e quanto rende ao ano."""

    class Meta:
        model = Conta
        fields = ["investido_inicial", "investido_em", "rendimento_anual"]
        labels = {"investido_inicial": "Quanto você tem guardado/investido", "investido_em": "Em que dia",
                  "rendimento_anual": "Rende quanto ao ano"}
        widgets = {
            "investido_inicial": _dinheiro(),
            "investido_em": DATA,
            "rendimento_anual": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100",
                                                         "inputmode": "decimal", "placeholder": "Ex.: 10,5"}),
        }

    def __init__(self, *args, hoje=None, **kwargs):
        self.hoje = hoje or date.today()
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.required = False
        self.fields["rendimento_anual"].help_text = ""

    def clean(self):
        dados = super().clean()
        valor, data, taxa = dados.get("investido_inicial"), dados.get("investido_em"), dados.get("rendimento_anual")
        if valor is not None and valor < 0:
            self.add_error("investido_inicial", "O valor não pode ser negativo.")
        if valor is not None and data is None:
            dados["investido_em"] = self.hoje          # sem data: o saldo é o de hoje
        if data and data > self.hoje:
            self.add_error("investido_em", "Informe uma data de hoje ou do passado.")
        if valor is None and data:
            self.add_error("investido_inicial", "Informe quanto havia investido nesse dia.")
        if taxa is not None and not 0 <= taxa <= 100:
            self.add_error("rendimento_anual", "Use um valor entre 0% e 100% ao ano.")
        return dados


class ConferenciaInvestimentoForm(forms.ModelForm):
    class Meta:
        model = ConferenciaInvestimento
        fields = ["data", "valor"]
        widgets = {"data": DATA, "valor": _dinheiro()}

    def __init__(self, *args, hoje=None, **kwargs):
        self.hoje = hoje or date.today()
        super().__init__(*args, **kwargs)

    def clean_data(self):
        data = self.cleaned_data["data"]
        if data > self.hoje:
            raise forms.ValidationError("Só dá para conferir o valor de hoje ou de dias passados.")
        return data

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor < 0:
            raise forms.ValidationError("O valor não pode ser negativo.")
        return valor
