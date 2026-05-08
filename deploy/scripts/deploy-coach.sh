#!/usr/bin/env bash
# deploy-coach.sh — builda o frontend-coach (no Mac) e publica na VPS.
#
# Por que rodar local e não na VPS?
#   - VPS pequena fica enxuta sem Node + node_modules (~250 MB)
#   - O build é totalmente determinístico (Vite com env de prod)
#   - Push do build é rápido (~370 KB de assets)
#
# Uso:
#   bash deploy/scripts/deploy-coach.sh <ssh-alias>
#
#   Onde <ssh-alias> é o Host configurado em ~/.ssh/config
#   (ex.: gym-deploy ou gym-vps).
#
# Pré-requisitos:
#   - Node 20+ instalado no Mac
#   - frontend-coach/ irmão da pasta backend-gymapp/ (mesmo diretório-pai)
#   - SSH funcionando pro alias passado (acesso de write na VPS)
#   - Compose já rodando na VPS (init-letsencrypt.sh + init-coach.sh OK)

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Uso: bash $0 <ssh-alias>" >&2
    echo "" >&2
    echo "Exemplo:  bash $0 gym-deploy" >&2
    exit 1
fi

SSH_ALIAS="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$(cd "$BACKEND_DIR/../frontend-coach" && pwd)"

if [[ ! -d "$FRONTEND_DIR" ]]; then
    echo "ERRO: frontend-coach/ não encontrado em $FRONTEND_DIR" >&2
    echo "       Esperado como irmão de backend-gymapp/." >&2
    exit 1
fi

echo "==> Build do frontend-coach em $FRONTEND_DIR"
cd "$FRONTEND_DIR"
if [[ ! -d node_modules ]]; then
    echo "    node_modules ausente — rodando npm install"
    npm install
fi
# Vite escolhe automaticamente .env.production pra `npm run build`
npm run build

if [[ ! -f dist/index.html ]]; then
    echo "ERRO: build não produziu dist/index.html" >&2
    exit 1
fi

echo "==> Upload do build pra VPS ($SSH_ALIAS:/tmp/coach-dist)"
ssh "$SSH_ALIAS" "mkdir -p /tmp/coach-dist && rm -rf /tmp/coach-dist/*"
rsync -avz --delete dist/ "$SSH_ALIAS:/tmp/coach-dist/"

echo "==> Copiando pro volume coach_dist (via container helper)"
# O volume `coach_dist` é montado read-only no nginx (boa prática de
# segurança), então usamos um alpine descartável pra escrever no volume:
# monta o volume + a pasta /tmp/coach-dist e copia o build pra dentro.
# Descobre o nome real do volume olhando pelo prefixo do projeto compose.
ssh "$SSH_ALIAS" bash -s <<'REMOTE_EOF'
set -euo pipefail
cd ~/backend-gymapp || cd ~/gym/backend-gymapp 2>/dev/null || { echo "Não achei a pasta do backend"; exit 1; }

# Descobre o nome do volume (Compose prefixa com o nome do projeto, ex.: backend-gymapp_coach_dist)
VOLUME=$(docker volume ls --format '{{.Name}}' | grep -E '_coach_dist$' | head -1)
if [[ -z "$VOLUME" ]]; then
    echo "ERRO: volume coach_dist não encontrado. O compose foi 'up' com docker-compose.prod.yml?" >&2
    exit 1
fi

echo "    volume: $VOLUME"
docker run --rm \
    -v "$VOLUME:/dst" \
    -v /tmp/coach-dist:/src:ro \
    alpine:3.20 \
    sh -c 'rm -rf /dst/* /dst/.[!.]* 2>/dev/null; cp -r /src/. /dst/ && ls -la /dst | head'

rm -rf /tmp/coach-dist
REMOTE_EOF

echo ""
echo "✓ Build publicado. Acesse https://\$COACH_DOMAIN pra confirmar."
