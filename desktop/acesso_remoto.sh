#!/bin/sh
# Configuração única para abrir e instalar o Ninho nos outros aparelhos, pelo Tailscale.
#   desktop/acesso_remoto.sh
#
# O Ninho continua rodando só neste computador; o Tailscale cria uma rede privada entre os seus
# aparelhos e publica o Ninho em https://<este-computador>.<sua-rede>.ts.net (HTTPS válido, que o
# navegador exige para instalar o app). Ninguém fora da sua rede Tailscale enxerga o endereço.
set -e
PORTA="${NINHO_PORTA:-8003}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "1/3 Instalando o Tailscale (vai pedir a sua senha)..."
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "1/3 Tailscale já instalado."
fi

echo
echo "2/3 Conectando este computador à sua conta Tailscale."
echo "    Se aparecer um link, abra no navegador e entre (Google, Microsoft, GitHub...)."
# --operator: o seu usuário passa a poder rodar "tailscale serve" sem sudo (o Ninho faz isso ao abrir)
sudo tailscale up --operator="$USER"

echo
echo "3/3 Publicando o Ninho (porta $PORTA) em HTTPS dentro da sua rede."
echo "    Se pedir para liberar HTTPS na conta, abra o link mostrado, confirme e rode este script de novo."
tailscale serve --bg "$PORTA"

ENDERECO="https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/"
echo
echo "Pronto! Nos outros aparelhos:"
echo "  1. Instale o Tailscale (tailscale.com/download ou loja de apps) e entre com a MESMA conta."
echo "     Para outra pessoa da família, convide-a para a sua rede no painel do Tailscale."
echo "  2. Abra $ENDERECO e entre com o seu usuário do Ninho."
echo "  3. Clique em \"Instalar app\"."
echo
echo "Reabra o Ninho neste computador para ele reconhecer o novo endereço."
