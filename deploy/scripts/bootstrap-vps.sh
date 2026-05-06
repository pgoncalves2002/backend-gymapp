#!/usr/bin/env bash
# bootstrap-vps.sh — roda 1 vez na VPS Ubuntu/Debian limpa.
# - instala Docker + Compose plugin
# - configura UFW (firewall) liberando 22, 80, 443
# - cria usuário "deploy" não-root e adiciona ao grupo docker
#
# Como rodar (logado como root via SSH):
#   curl -fsSL https://raw.githubusercontent.com/<seu-user>/<seu-repo>/main/backend-gymapp/deploy/scripts/bootstrap-vps.sh | bash
# OU já com o repo clonado:
#   sudo bash backend-gymapp/deploy/scripts/bootstrap-vps.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Este script precisa ser rodado como root (sudo)." >&2
   exit 1
fi

echo "==> Atualizando o sistema"
apt-get update -y
apt-get upgrade -y

echo "==> Instalando dependências básicas"
apt-get install -y ca-certificates curl gnupg lsb-release ufw git

echo "==> Instalando Docker (se ainda não tiver)"
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
        $(. /etc/os-release; echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi

echo "==> Configurando UFW (firewall)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Criando usuário 'deploy' (se ainda não existir)"
if ! id -u deploy &>/dev/null; then
    adduser --disabled-password --gecos "" deploy
    usermod -aG docker deploy
    # Copia chaves SSH do root pra deploy (se houver), pra você logar direto como deploy
    if [[ -f /root/.ssh/authorized_keys ]]; then
        mkdir -p /home/deploy/.ssh
        cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
        chown -R deploy:deploy /home/deploy/.ssh
        chmod 700 /home/deploy/.ssh
        chmod 600 /home/deploy/.ssh/authorized_keys
    fi
fi

echo ""
echo "✓ Bootstrap concluído."
echo ""
echo "Próximos passos:"
echo "  1) Faça logout e logue de novo como deploy:  ssh deploy@<ip-da-vps>"
echo "  2) Clone o repo:                              git clone <seu-repo> ~/gym"
echo "  3) cd ~/gym/backend-gymapp"
echo "  4) Copie e ajuste:                            cp .env.prod.example .env.prod  &&  vim .env.prod"
echo "  5) Rode:                                      bash deploy/scripts/init-letsencrypt.sh"
echo "  6) Rode:                                      bash deploy/scripts/deploy.sh"
echo ""
echo "Detalhes em backend-gymapp/deploy/README.md."
