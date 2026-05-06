#!/usr/bin/env bash
# deploy.sh — atualiza o app na VPS pra última versão do main.
#
# Roda na VPS, na pasta do projeto. Idempotente.
#
# Uso:
#   bash backend-django/deploy/scripts/deploy.sh

set -euo pipefail

cd "$(dirname "$0")/../.."  # pasta backend-django/

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"

echo "==> Puxando alterações do Git"
git -C "$(git rev-parse --show-toplevel)" pull --ff-only

echo "==> Reconstruindo imagem do app"
$COMPOSE build web

echo "==> Subindo serviços"
$COMPOSE up -d

echo "==> Aplicando migrations"
$COMPOSE exec -T web python manage.py migrate --noinput

echo "==> Coletando arquivos estáticos"
$COMPOSE exec -T web python manage.py collectstatic --noinput

echo "==> Recarregando nginx (caso config tenha mudado)"
$COMPOSE exec nginx nginx -s reload || true

echo ""
echo "✓ Deploy concluído."
$COMPOSE ps
