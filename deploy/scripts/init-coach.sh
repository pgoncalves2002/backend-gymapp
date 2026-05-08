#!/usr/bin/env bash
# init-coach.sh — emite cert Let's Encrypt pro subdomínio do SPA do personal.
#
# Roda DEPOIS do init-letsencrypt.sh (que já subiu nginx + certbot).
# Adiciona o cert do COACH_DOMAIN sem mexer no cert do DJANGO_DOMAIN existente.
#
# Idempotente — se já tem cert real, só sai.
#
# Pré-requisitos:
#   - init-letsencrypt.sh já rodou com sucesso (containers rodando).
#   - DNS do COACH_DOMAIN apontando pro IP da VPS (verifique com `dig +short ${COACH_DOMAIN}`).
#   - Build do frontend já rsyncado pro volume `coach_dist` (via deploy-coach.sh)
#     OU o /srv/coach pode estar vazio nesse momento (o nginx só serve um 404
#     no SPA até o build chegar; o cert é emitido independente).

set -euo pipefail

cd "$(dirname "$0")/../.."  # pasta backend-gymapp/

if [[ ! -f .env.prod ]]; then
    echo "ERRO: .env.prod não existe." >&2
    exit 1
fi

set -a; source .env.prod; set +a

DOMAIN="${COACH_DOMAIN:-}"
EMAIL="${LETSENCRYPT_EMAIL}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"
LIVE_PATH="/etc/letsencrypt/live/${DOMAIN}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
    echo "ERRO: defina COACH_DOMAIN e LETSENCRYPT_EMAIL no .env.prod" >&2
    exit 1
fi

# COACH_DOMAIN tem que ser FQDN puro (ex.: coach.foo.com), sem protocolo.
# Cair pra Let's Encrypt com "https://..." ou "http://..." gera erro
# "appears to be a URL, not a FQDN" + cria pastas bagunçadas em /etc/letsencrypt.
if [[ "$DOMAIN" == *://* ]]; then
    echo "ERRO: COACH_DOMAIN não pode ter protocolo. Valor atual: '$DOMAIN'" >&2
    echo "      Edite .env.prod removendo 'http://' ou 'https://' do começo." >&2
    echo "      Ex. correto: COACH_DOMAIN=coach.seudominio.com.br" >&2
    exit 1
fi

echo "==> Subdomínio do SPA: $DOMAIN"

# Confirma DNS antes de gastar quota do Let's Encrypt.
if ! getent hosts "$DOMAIN" >/dev/null 2>&1; then
    echo "AVISO: DNS pra $DOMAIN ainda não resolve neste host." >&2
    echo "       O Let's Encrypt vai falhar até o A/CNAME propagar." >&2
fi

# Já tem cert real? sai limpo.
if $COMPOSE run --rm --entrypoint "test -f $LIVE_PATH/fullchain.pem -a -L $LIVE_PATH/fullchain.pem" certbot 2>/dev/null; then
    echo "==> Certificado real já existe pra $DOMAIN. Nada a fazer."
    exit 0
fi

# Cria cert dummy só pra nginx aceitar o server-block 443 do coach
# enquanto a gente pede o cert real (caso o nginx já esteja rodando, ele
# vai recarregar no fim).
echo "==> Criando cert dummy em $LIVE_PATH (pra nginx não falhar ao recarregar)"
$COMPOSE run --rm --entrypoint "\
    sh -c 'mkdir -p $LIVE_PATH && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout $LIVE_PATH/privkey.pem \
        -out    $LIVE_PATH/fullchain.pem \
        -subj /CN=localhost'" certbot

echo "==> Recarregando nginx pra reconhecer o vhost de $DOMAIN"
$COMPOSE up -d
$COMPOSE exec nginx nginx -s reload || true

echo "==> Removendo cert dummy"
$COMPOSE run --rm --entrypoint "\
    sh -c 'rm -rf /etc/letsencrypt/live/$DOMAIN && \
           rm -rf /etc/letsencrypt/archive/$DOMAIN && \
           rm -rf /etc/letsencrypt/renewal/$DOMAIN.conf'" certbot

echo "==> Solicitando cert real pra $DOMAIN"
$COMPOSE run --rm --entrypoint "\
    certbot certonly --webroot -w /var/www/certbot \
        --email $EMAIL \
        -d $DOMAIN \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        --rsa-key-size 2048 \
        --force-renewal" certbot

echo "==> Recarregando nginx com cert real"
$COMPOSE exec nginx nginx -s reload

echo ""
echo "✓ Pronto. Cert emitido pra $DOMAIN."
echo "  Pra publicar o build do frontend, rode (no Mac local):"
echo "    bash deploy/scripts/deploy-coach.sh <ssh-alias-da-vps>"
