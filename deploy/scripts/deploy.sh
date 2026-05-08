#!/usr/bin/env bash
# deploy.sh — atualiza o app na VPS pra última versão do main.
#
# Roda na VPS, na pasta do projeto. Idempotente.
#
# Uso:
#   bash backend-gymapp/deploy/scripts/deploy.sh
#
# Comportamento:
#   1. Tira backup do banco (via backup-db.sh) — sempre, antes de qualquer mudança
#   2. Resolve colisão de migrations untracked vs. as do commit (sem perda)
#   3. git pull
#   4. Rebuild + up + migrate + collectstatic + reload nginx
#
# O volume `pgdata` é preservado em TODO o fluxo — nenhum comando aqui usa
# `down -v`, que seria o único capaz de apagar o banco. O `up -d` apenas
# recria os containers se a config/imagem mudou; volumes são reaproveitados.

set -euo pipefail

cd "$(dirname "$0")/../.."  # pasta backend-gymapp/

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"

# ---------------------------------------------------------------------------
# 1) Backup do banco SEMPRE antes de qualquer mudança
# ---------------------------------------------------------------------------
echo "==> Tirando backup do banco antes do deploy"
bash deploy/scripts/backup-db.sh

# ---------------------------------------------------------------------------
# 2) Resolve colisão de migrations untracked
# ---------------------------------------------------------------------------
# Se a VPS tem arquivos de migration gerados localmente lá com
# `makemigrations` que estão untracked, vão colidir com o `git pull`
# quando alguém commitar elas no main. Estratégia: mover as untracked
# pra .deploy-backup-<ts>/, pullar, comparar conteúdo. Se idênticas,
# limpa o backup; se diferentes, para e mostra diff pra investigação.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

UNTRACKED_MIGRATIONS=$(git ls-files --others --exclude-standard '*/migrations/*.py' || true)
BACKUP_DIR=""
if [[ -n "$UNTRACKED_MIGRATIONS" ]]; then
    echo "==> Migrations untracked detectadas — movendo pra .deploy-backup/"
    BACKUP_DIR=".deploy-backup-$(date +%s)"
    mkdir -p "$BACKUP_DIR"
    while IFS= read -r f; do
        echo "    $f"
        mkdir -p "$BACKUP_DIR/$(dirname "$f")"
        mv "$f" "$BACKUP_DIR/$f"
    done <<< "$UNTRACKED_MIGRATIONS"
fi

# ---------------------------------------------------------------------------
# 3) Git pull
# ---------------------------------------------------------------------------
echo "==> Puxando alterações do Git"
git pull --ff-only

# Compara migrations que a VPS tinha com as que vieram do commit
if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
    echo "==> Comparando migrations da VPS com as do commit"
    while IFS= read -r f; do
        if [[ -f "$f" && -f "$BACKUP_DIR/$f" ]]; then
            if ! diff -q "$f" "$BACKUP_DIR/$f" >/dev/null; then
                echo "ALERTA: $f difere do que estava na VPS!" >&2
                echo "  diff (esquerdo: VPS / direito: commit):" >&2
                diff "$BACKUP_DIR/$f" "$f" | head -40 >&2 || true
                echo "" >&2
                echo "  Investigue manualmente. Backup em $BACKUP_DIR/" >&2
                exit 1
            fi
        fi
    done <<< "$UNTRACKED_MIGRATIONS"
    echo "    ✓ todas idênticas — limpando $BACKUP_DIR"
    rm -rf "$BACKUP_DIR"
fi

# ---------------------------------------------------------------------------
# 4) Build + up + migrate + collectstatic + reload
# ---------------------------------------------------------------------------
echo "==> Reconstruindo imagem do app"
$COMPOSE build web

echo "==> Subindo serviços (volume pgdata preservado)"
$COMPOSE up -d

echo "==> Aplicando migrations (já aplicadas → 'already applied', sem efeito)"
$COMPOSE exec -T web python manage.py migrate --noinput

echo "==> Coletando arquivos estáticos"
$COMPOSE exec -T web python manage.py collectstatic --noinput

echo "==> Recarregando nginx (caso config tenha mudado)"
$COMPOSE exec nginx nginx -s reload || true

echo ""
echo "✓ Deploy concluído."
$COMPOSE ps
