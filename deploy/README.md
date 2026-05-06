# Deploy — passo a passo

Do "VPS Ubuntu vazia" até "API rodando em `https://api.seudominio.com.br`".

> **Estrutura de produção:** Postgres + Django (gunicorn) + Nginx (reverse proxy + estáticos + media) + Certbot (Let's Encrypt). Tudo via Docker Compose.

---

## Pré-requisitos

- VPS Ubuntu 22.04+ ou Debian 12+ com **acesso SSH como root** (ou outro user com sudo)
- **Domínio** com registro `A` apontando pro IP da VPS (verifique antes: `dig +short api.seudominio.com.br` deve retornar o IP)
- Repositório Git (GitHub/GitLab) com o projeto

---

## 1) Subir o código pro GitHub

O `git init` já foi feito dentro de `backend-django/` e o primeiro commit também. Agora basta:

```bash
cd ~/Desktop/gym/backend-django

# Crie um repo vazio no GitHub (sem README, sem .gitignore — eu já gerei).
# Depois conecte e faça push:
git remote add origin git@github.com:<seu-user>/<seu-repo>.git
git push -u origin main
```

> **Importante:** `.env` e `.env.prod` estão no `.gitignore` — não vão pro repositório. As credenciais de produção ficam só na VPS.
> O app Swift (pasta `gym/`) ficou de fora deste repo (já tem o próprio `.git` do Xcode). Pra manter os dois sincronizados, você pode tratá-los como repos separados ou criar um monorepo depois.

---

## 2) Bootstrap da VPS (1ª vez, como root)

SSH na VPS e rode:

```bash
ssh root@<ip-da-vps>

# Opção A — script via curl direto do GitHub
curl -fsSL https://raw.githubusercontent.com/<seu-user>/<seu-repo>/main/deploy/scripts/bootstrap-vps.sh | bash

# Opção B — clone primeiro e rode local
apt-get update && apt-get install -y git
git clone https://github.com/<seu-user>/<seu-repo>.git /opt/gym-backend
bash /opt/gym-backend/deploy/scripts/bootstrap-vps.sh
```

O que esse script faz:
- Instala Docker + Compose plugin
- Habilita UFW liberando 22 (SSH), 80, 443
- Cria usuário `deploy` (sem senha, login só por SSH key)

Depois disso, **logue novamente como `deploy`**:

```bash
ssh deploy@<ip-da-vps>
```

---

## 3) Clonar o projeto e configurar ambiente

```bash
git clone https://github.com/<seu-user>/<seu-repo>.git ~/gym-backend
cd ~/gym-backend

# Copia o template e edita
cp .env.prod.example .env.prod
nano .env.prod   # ou vim
```

**O que ajustar no `.env.prod`:**
- `DJANGO_DOMAIN=api.seudominio.com.br`
- `LETSENCRYPT_EMAIL=voce@seudominio.com.br`
- `DJANGO_SECRET_KEY` — gere com:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
- `DJANGO_ALLOWED_HOSTS=api.seudominio.com.br`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://api.seudominio.com.br`
- `POSTGRES_PASSWORD` — senha forte, única desta instalação

---

## 4) Obter certificado HTTPS e subir tudo

```bash
bash deploy/scripts/init-letsencrypt.sh
```

Esse script:
1. Cria certificados dummy (pra nginx subir)
2. Sobe os containers
3. Substitui pelos certificados reais via Let's Encrypt
4. Recarrega nginx

Idempotente — se rodar de novo e já tiver cert real, ele só sobe normalmente.

---

## 5) Migrations e superuser

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod"

$COMPOSE exec web python manage.py migrate
$COMPOSE exec web python manage.py collectstatic --noinput
$COMPOSE exec web python manage.py createsuperuser
```

Pronto. Acesse `https://api.seudominio.com.br/admin/`.

---

## 6) Atualizações futuras (deploy contínuo)

A cada push no `main`:

```bash
ssh deploy@<ip-da-vps>
cd ~/gym
bash deploy/scripts/deploy.sh
```

O `deploy.sh` faz:
- `git pull`
- Rebuild da imagem do `web`
- Restart dos containers
- `migrate` + `collectstatic`
- Reload do nginx

---

## Troubleshooting

**`init-letsencrypt.sh` falha em "Failed authorization procedure"**
DNS ainda não propagou. Confirme com `dig +short api.seudominio.com.br` e espere até o IP retornar correto antes de rodar de novo.

**`nginx: [emerg] cannot load certificate`**
Os certs dummy não foram criados — apague tudo e refaça:
```bash
docker compose down -v
bash deploy/scripts/init-letsencrypt.sh
```

**Quero ver os logs**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod logs -f web
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod logs -f nginx
```

**Banco caiu / quero backup**
```bash
# Dump
docker compose exec db pg_dump -U gym gym > backup-$(date +%F).sql

# Restore (com o container db rodando)
cat backup.sql | docker compose exec -T db psql -U gym gym
```

**Mudei o config do nginx e quero recarregar sem downtime**
```bash
docker compose exec nginx nginx -t   # valida config
docker compose exec nginx nginx -s reload
```

---

## Apontar o app Swift pra produção

Em `gym/gym/gym/Services/APIClient.swift`:

```swift
static let baseURL = URL(string: "https://api.seudominio.com.br")!
```

E **remova** o `NSAllowsArbitraryLoads` do `Info.plist` — em HTTPS não precisa mais.
