import calendar
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

from .models import Aporte, AssinaturaCartao, Cartao, CompraCartao, PagamentoConta, Conferencia, Conta, ContaFixa, Familia, Lancamento, UsoDiario

DATA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _dinheiro(placeholder="0,00"):
    return forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal",
                                    "placeholder": placeholder})


class ContaForm(forms.ModelForm):
    class Meta:
        model = Conta
        fields = ["nome", "saldo_inicial", "data_inicio",
                  "modo_gasto", "percentual_gasto", "gasto_mensal",
                  "guardar_modo", "guardar_percentual", "guardar_valor"]
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gasto_mensal"].required = False
        self.fields["guardar_modo"].required = False

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
        return dados


class PagamentoSalarioForm(forms.ModelForm):
    """Um pagamento do salário: o dia do mês em que cai e quanto cai."""

    class Meta:
        model = ContaFixa
        fields = ["dia", "valor"]
        labels = {"dia": "Dia", "valor": "Valor"}
        help_texts = {"dia": ""}
        widgets = {
            "dia": forms.NumberInput(attrs={"min": "1", "max": "31", "inputmode": "numeric", "placeholder": "Dia"}),
            "valor": _dinheiro(),
        }

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
            p.nome = "Salário" if len(mantidos) == 1 else f"Salário (dia {p.dia})"
            p.save()
        return pagamentos


PagamentosSalarioFormSet = forms.inlineformset_factory(
    Conta, ContaFixa, form=PagamentoSalarioForm, formset=BasePagamentosSalario,
    extra=0, can_delete=True,
)


class ContaFixaForm(forms.ModelForm):
    dia = forms.TypedChoiceField(
        label="Dia do mês", coerce=int, choices=[(d, d) for d in range(1, 32)],
        widget=forms.RadioSelect,
        error_messages={"required": "Escolha o dia do mês.", "invalid_choice": "Escolha um dia entre 1 e 31."},
    )

    class Meta:
        model = ContaFixa
        fields = ["tipo", "nome", "variavel", "valor", "dia", "ativa"]
        labels = {"tipo": "Tipo", "nome": "Descrição", "valor": "Valor", "ativa": "Ativa",
                  "variavel": "O valor muda todo mês"}
        help_texts = {"ativa": "Desligada, deixa de contar em todos os meses, inclusive nos que já passaram.",
                      "variavel": "Luz, água, mercado… O valor abaixo vira uma estimativa, e a cada mês "
                                  "você marca como paga informando o valor real."}
        widgets = {
            "tipo": forms.RadioSelect,
            "nome": forms.TextInput(attrs={"placeholder": "Ex.: Aluguel, Internet, Energia", "autocomplete": "off"}),
            "valor": _dinheiro(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            del self.fields["ativa"]  # novas já nascem ativas
        if self.instance.salario:
            del self.fields["tipo"]      # pagamento de salário é sempre receita
            del self.fields["variavel"]  # e tem valor definido na configuração da conta

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor


class LancamentoForm(forms.ModelForm):
    class Meta:
        model = Lancamento
        fields = ["tipo", "descricao", "valor", "data"]
        labels = {"tipo": "Tipo", "descricao": "Descrição", "valor": "Valor", "data": "Data"}
        widgets = {
            "tipo": forms.RadioSelect,
            "descricao": forms.TextInput(attrs={"placeholder": "Ex.: Conserto do carro, Freela", "autocomplete": "off"}),
            "valor": _dinheiro(),
            "data": DATA,
        }

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor


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


class CompraCartaoForm(forms.ModelForm):
    parcelas = forms.IntegerField(
        label="Parcelas", min_value=1, max_value=48, initial=1,
        widget=forms.NumberInput(attrs={"min": "1", "max": "48", "inputmode": "numeric"}),
        error_messages={"min_value": "Pelo menos 1 parcela.", "max_value": "No máximo 48 parcelas."},
    )

    class Meta:
        model = CompraCartao
        fields = ["cartao", "descricao", "valor_total", "parcelas", "data_compra"]
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

    def clean_valor_total(self):
        valor = self.cleaned_data["valor_total"]
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor


class AssinaturaForm(forms.ModelForm):
    dia_cobranca = _dia_do_mes("Dia da cobrança")

    class Meta:
        model = AssinaturaCartao
        fields = ["cartao", "descricao", "valor", "dia_cobranca", "inicio", "fim"]
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
        fields = ["data", "valor", "descricao"]

    def __init__(self, *args, conta, hoje, **kwargs):
        self.conta, self.hoje = conta, hoje
        super().__init__(*args, **kwargs)
        self.fields["descricao"].required = False

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
