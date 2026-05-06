#!/usr/bin/env bash
# init-letsencrypt.sh — obtém o primeiro certificado Let's Encrypt.
#
# Estratégia (baseada no projeto wmnnd/nginx-certbot):
#   1) Cria certificados *dummy* pra nginx subir (ele exige cert pra abrir 443).
#   2) Sobe nginx + web + db.
#   3) Apaga os certs dummy e roda certbot pra obter os certs reais via webroot.
#   4) Reinicia nginx pra recarregar com os certs reais.
#
# Idempotente — se já tem cert real, simplesmente não faz nada.
#
# Pré-requisitos:
#   - .env.prod preenchido (DJANGO_DOMAIN, LETSENCRYPT_EMAIL, etc.)
#   - DNS do domínio JÁ apontando pro IP da VPS (verifique com `dig +short ${DOMAIN}`).
#   - Portas 80 e 443 abertas no firewall (bootstrap-vps.sh já faz isso).

set -euo pipefail

cd "$(dirname "$0")/../.."  # pasta backend-gymapp/

if [[ ! -f .env.prod ]]; then
    echo "ERRO: .env.prod não existe. Copie de .env.prod.example e preencha." >&2
    exit 1
fi

set -a; source .env.prod; set +a

DOMAIN="${DJANGO_DOMAIN}"
EMAIL="${LETSENCRYPT_EMAIL}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"
LIVE_PATH="/etc/letsencrypt/live/${DOMAIN}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
    echo "ERRO: defina DJANGO_DOMAIN e LETSENCRYPT_EMAIL no .env.prod" >&2
    exit 1
fi

echo "==> Domínio: $DOMAIN"
echo "==> Email Let's Encrypt: $EMAIL"

# Cria volumes se ainda não existirem.
$COMPOSE up --no-start nginx certbot >/dev/null 2>&1 || true

# Já tem cert real? então só sobe e sai.
if $COMPOSE run --rm --entrypoint "test -f $LIVE_PATH/fullchain.pem -a -L $LIVE_PATH/fullchain.pem" certbot 2>/dev/null; then
    echo "==> Certificado real já existe pra $DOMAIN. Subindo serviços normalmente."
    $COMPOSE up -d
    exit 0
fi

echo "==> Criando certificado dummy temporário em $LIVE_PATH"
$COMPOSE run --rm --entrypoint "\
    sh -c 'mkdir -p $LIVE_PATH && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout $LIVE_PATH/privkey.pem \
        -out    $LIVE_PATH/fullchain.pem \
        -subj /CN=localhost'" certbot

echo "==> Subindo serviços (nginx vai abrir com o cert dummy)"
$COMPOSE up -d

echo "==> Removendo cert dummy"
$COMPOSE run --rm --entrypoint "\
    sh -c 'rm -rf /etc/letsencrypt/live/$DOMAIN && \
           rm -rf /etc/letsencrypt/archive/$DOMAIN && \
           rm -rf /etc/letsencrypt/renewal/$DOMAIN.conf'" certbot

echo "==> Solicitando certificado real pra $DOMAIN"
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
echo "✓ Pronto. Acesse https://$DOMAIN/admin/ pra confirmar."
