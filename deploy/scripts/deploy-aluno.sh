#!/usr/bin/env bash
# deploy-aluno.sh — builda o frontend-aluno (no Mac) e publica na VPS.
#
# Por que rodar local e não na VPS?
#   - VPS pequena fica enxuta sem Node + node_modules (~250 MB)
#   - O build é totalmente determinístico (Vite com env de prod)
#   - Push do build é rápido (~370 KB de assets)
#
# Uso:
#   bash deploy/scripts/deploy-aluno.sh <ssh-alias>
#
#   Onde <ssh-alias> é o Host configurado em ~/.ssh/config
#   (ex.: gym-deploy ou gym-vps).
#
# Pré-requisitos:
#   - Node 20+ instalado no Mac
#   - frontend-aluno/ irmão da pasta backend-gymapp/ (mesmo diretório-pai)
#   - SSH funcionando pro alias passado (acesso de write na VPS)
#   - Compose já rodando na VPS (init-letsencrypt.sh + init-aluno.sh OK)

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
FRONTEND_DIR="$(cd "$BACKEND_DIR/../frontend-aluno" && pwd)"

if [[ ! -d "$FRONTEND_DIR" ]]; then
    echo "ERRO: frontend-aluno/ não encontrado em $FRONTEND_DIR" >&2
    echo "       Esperado como irmão de backend-gymapp/." >&2
    exit 1
fi

echo "==> Build do frontend-aluno em $FRONTEND_DIR"
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

echo "==> Upload do build pra VPS ($SSH_ALIAS:/tmp/aluno-dist)"
ssh "$SSH_ALIAS" "mkdir -p /tmp/aluno-dist && rm -rf /tmp/aluno-dist/*"
rsync -avz --delete dist/ "$SSH_ALIAS:/tmp/aluno-dist/"

echo "==> Copiando pro volume aluno_dist (via container helper)"
# O volume `aluno_dist` é montado read-only no nginx (boa prática de
# segurança), então usamos um alpine descartável pra escrever no volume:
# monta o volume + a pasta /tmp/aluno-dist e copia o build pra dentro.
# Descobre o nome real do volume olhando pelo prefixo do projeto compose.
ssh "$SSH_ALIAS" bash -s <<'REMOTE_EOF'
set -euo pipefail
cd ~/backend-gymapp || cd ~/gym/backend-gymapp 2>/dev/null || { echo "Não achei a pasta do backend"; exit 1; }

# Descobre o nome do volume (Compose prefixa com o nome do projeto, ex.: backend-gymapp_aluno_dist)
VOLUME=$(docker volume ls --format '{{.Name}}' | grep -E '_aluno_dist$' | head -1)
if [[ -z "$VOLUME" ]]; then
    echo "ERRO: volume aluno_dist não encontrado. O compose foi 'up' com docker-compose.prod.yml?" >&2
    exit 1
fi

echo "    volume: $VOLUME"
docker run --rm \
    -v "$VOLUME:/dst" \
    -v /tmp/aluno-dist:/src:ro \
    alpine:3.20 \
    sh -c 'rm -rf /dst/* /dst/.[!.]* 2>/dev/null; cp -r /src/. /dst/ && ls -la /dst | head'

rm -rf /tmp/aluno-dist
REMOTE_EOF

# Lê o ALUNO_DOMAIN diretamente do .env.prod da VPS pra mostrar a URL final.
COACH_URL=$(ssh "$SSH_ALIAS" "grep '^ALUNO_DOMAIN=' ~/backend-gymapp/.env.prod 2>/dev/null | cut -d= -f2-" || true)

echo ""
if [[ -n "$COACH_URL" ]]; then
    echo "✓ Build publicado. Acesse https://$COACH_URL pra confirmar."
else
    echo "✓ Build publicado."
fi
