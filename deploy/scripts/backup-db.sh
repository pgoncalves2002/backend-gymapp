#!/usr/bin/env bash
# backup-db.sh — pg_dump completo do banco de produção.
#
# Roda na VPS, na pasta do projeto. Idempotente.
# Saída: backups/gym-YYYY-MM-DD-HHMM.sql.gz (gitignorado)
#
# Uso:
#   bash deploy/scripts/backup-db.sh
#
# Restore:
#   gunzip -c backups/gym-2026-05-08-1700.sql.gz | \
#     docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#       --env-file .env.prod exec -T db psql -U gym gym

set -euo pipefail

cd "$(dirname "$0")/../.."  # pasta backend-gymapp/

if [[ ! -f .env.prod ]]; then
    echo "ERRO: .env.prod não existe." >&2
    exit 1
fi

set -a; source .env.prod; set +a

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"
TS=$(date +%Y-%m-%d-%H%M)
OUT="backups/gym-${TS}.sql"

mkdir -p backups

echo "==> Tirando dump de $POSTGRES_DB (user $POSTGRES_USER) → $OUT"
$COMPOSE exec -T db pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --clean --if-exists --no-owner --no-privileges \
    > "$OUT"

# Tamanho
SIZE=$(du -h "$OUT" | cut -f1)
echo "    raw: $SIZE"

# Comprime
gzip "$OUT"
SIZE_GZ=$(du -h "${OUT}.gz" | cut -f1)
echo "    gzip: $SIZE_GZ"

# Lista os 5 mais recentes pra orientar limpeza
echo ""
echo "==> Backups recentes em backups/:"
ls -lht backups/*.sql.gz 2>/dev/null | head -5

echo ""
echo "✓ Backup pronto: ${OUT}.gz"
echo ""
echo "Pra restaurar (depois de derrubar tudo):"
echo "  gunzip -c ${OUT}.gz | \\"
echo "    $COMPOSE exec -T db psql -U $POSTGRES_USER $POSTGRES_DB"
