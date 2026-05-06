#!/usr/bin/env bash
# bootstrap-vps.sh — roda 1 vez na VPS Ubuntu/Debian limpa.
# - instala Docker + Compose plugin
# - configura UFW (firewall) liberando a porta SSH atual + 80 + 443
# - cria usuário "deploy" não-root e adiciona ao grupo docker
#
# Como rodar (logado como root via SSH):
#   curl -fsSL https://raw.githubusercontent.com/<seu-user>/<seu-repo>/main/deploy/scripts/bootstrap-vps.sh | bash
# OU já com o repo clonado:
#   sudo bash deploy/scripts/bootstrap-vps.sh
#
# Se sua VPS usa porta SSH não-padrão (HostGator usa 22022), pode passar via env:
#   sudo SSH_PORT=22022 bash deploy/scripts/bootstrap-vps.sh
# Mas o default é detectar automaticamente lendo /etc/ssh/sshd_config.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Este script precisa ser rodado como root (sudo)." >&2
   exit 1
fi

# Detecta a porta SSH ativa (lê do sshd_config) ou usa o que veio via env.
# Cuidado: liberar a porta errada faria você ser CORTADO do SSH ao habilitar UFW.
detect_ssh_port() {
    local port
    port="$(awk '/^Port[[:space:]]+[0-9]+/ {print $2; exit}' /etc/ssh/sshd_config 2>/dev/null || true)"
    echo "${port:-22}"
}
SSH_PORT="${SSH_PORT:-$(detect_ssh_port)}"
echo "==> Porta SSH detectada: $SSH_PORT (será liberada no firewall)"

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
# Libera a porta SSH descoberta — NÃO usa o perfil 'OpenSSH' (assume porta 22).
ufw allow "${SSH_PORT}/tcp"
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
echo "  1) Faça logout e logue de novo como deploy:  ssh -p ${SSH_PORT} deploy@<ip-da-vps>"
echo "  2) Clone o repo:                              git clone <seu-repo> ~/backend-gymapp"
echo "  3) cd ~/backend-gymapp"
echo "  4) Copie e ajuste:                            cp .env.prod.example .env.prod  &&  nano .env.prod"
echo "  5) Rode:                                      bash deploy/scripts/init-letsencrypt.sh"
echo "  6) Rode:                                      bash deploy/scripts/deploy.sh"
echo ""
echo "Detalhes em deploy/README.md."
