"""Categorias de gasto: as padrão de cada família e a sugestão pela descrição.

Sem dependência dos models: a migração que classifica os dados antigos também usa este módulo.
"""

import re
import unicodedata

# (nome, palavras-chave). A ordem é a ordem de exibição; "Outros" fica por último.
PADRAO = [
    ("Mercado", ["mercado", "supermercado", "atacadao", "atacadista", "assai", "carrefour", "pao de acucar",
                 "hortifruti", "sacolao", "feira", "acougue", "padaria", "quitanda", "mercearia", "dia supermercado",
                 "nagumo", "sonda", "hirota", "oba hortifruti", "st marche", "mambo", "savegnago", "guanabara",
                 "prezunic", "zona sul", "bistek", "angeloni", "condor", "muffato", "big", "makro", "sams club",
                 "atacadao", "tenda atacado", "roldao", "spani"]),
    ("Alimentação fora", ["ifood", "rappi", "restaurante", "lanche", "lanchonete", "almoco", "jantar", "pizza",
                          "pizzaria", "hamburguer", "burger", "mcdonalds", "mc donalds", "bk", "subway", "cafe",
                          "cafeteria", "delivery", "sorvete", "acai", "bar", "boteco", "marmita", "salgado"]),
    ("Transporte", ["uber", "99", "99pop", "taxi", "onibus", "metro", "trem", "bilhete unico", "combustivel",
                    "gasolina", "etanol", "alcool", "diesel", "posto", "estacionamento", "pedagio", "sem parar",
                    "veloe", "oficina", "mecanico", "ipva", "licenciamento", "revisao do carro", "produtos do carro",
                    "lava jato", "lavagem", "pneu", "seguro do carro", "seguro carro", "seguro auto", "abastecer",
                    "abastecimento", "shell", "ipiranga", "petrobras", "br mania", "bike", "patinete"]),
    ("Casa", ["aluguel", "condominio", "luz", "energia", "enel", "cemig", "copel", "light", "agua", "sabesp",
              "saneamento", "gas", "internet", "fibra", "celular", "telefone", "iptu", "faxina", "diarista",
              "moveis", "manutencao", "conserto", "reforma", "material de construcao", "leroy", "eletricista",
              "encanador", "limpeza", "produto de limpeza", "alarme", "verisure", "seguro residencial", "chacara"]),
    ("Saúde", ["farmacia", "drogaria", "droga raia", "drogasil", "pague menos", "remedio", "medico", "consulta",
               "exame", "laboratorio", "dentista", "plano de saude", "unimed", "amil", "bradesco saude",
               "sulamerica", "hapvida", "hospital", "psicologo", "terapia", "academia", "smart fit", "smartfit",
               "fisioterapia", "otica", "oculos"]),
    ("Educação", ["escola", "faculdade", "universidade", "curso", "livro", "livraria", "material escolar",
                  "mensalidade escolar", "udemy", "alura", "idiomas", "ingles"]),
    ("Lazer", ["cinema", "show", "teatro", "ingresso", "viagem", "passagem", "hotel", "pousada", "airbnb",
               "booking", "passeio", "parque", "steam", "playstation", "xbox", "nintendo", "jogo", "festa", "clube",
               "latam", "latam pass", "smiles", "decolar", "123milhas", "rifa"]),
    ("Assinaturas", ["netflix", "spotify", "disney", "prime video", "amazon prime", "hbo", "max", "globoplay",
                     "youtube premium", "apple", "icloud", "google one", "deezer", "paramount", "crunchyroll",
                     "chatgpt", "claude", "assinatura", "streaming", "kindle", "audible"]),
    ("Compras", ["shopee", "mercado livre", "mercadolivre", "amazon", "magalu", "magazine luiza", "americanas",
                 "shein", "aliexpress", "roupa", "roupas", "calcado", "sapato", "tenis", "loja", "presente",
                 "eletronico", "eletrodomestico", "casas bahia", "renner", "riachuelo", "c&a", "zara"]),
    ("Cuidados pessoais", ["maquiagem", "cosmetico", "cosmeticos", "perfume", "perfumaria", "boticario",
                           "natura", "sephora", "salao", "salao de beleza", "cabeleireiro", "barbearia",
                           "corte de cabelo", "manicure", "unha", "depilacao", "estetica", "skincare"]),
    ("Pets", ["racao", "pet", "petshop", "pet shop", "veterinario", "banho e tosa", "petz", "cobasi"]),
    ("Taxas e juros", ["tarifa", "juros", "iof", "anuidade", "multa", "imposto", "taxa", "encargos",
                       "cesta de servicos", "mora"]),
    ("Outros", []),
]

NOMES_PADRAO = [nome for nome, _ in PADRAO]


def normalizar(texto):
    """Minúsculas, sem acentos e com espaços simples: "  Almoço  " -> "almoco"."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def _regras():
    # palavras mais longas primeiro: "amazon prime" (Assinaturas) ganha de "amazon" (Compras)
    regras = [(normalizar(palavra), nome) for nome, palavras in PADRAO for palavra in palavras]
    regras.sort(key=lambda r: len(r[0]), reverse=True)
    return [(re.compile(rf"(?<![a-z0-9]){re.escape(palavra)}(?![a-z0-9])"), nome) for palavra, nome in regras]


REGRAS = _regras()


def categoria_pelas_regras(descricao):
    """Nome da categoria padrão sugerida pela descrição, ou None."""
    texto = normalizar(descricao)
    if not texto:
        return None
    for padrao, nome in REGRAS:
        if padrao.search(texto):
            return nome
    return None
