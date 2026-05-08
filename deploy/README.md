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

O `git init` já foi feito dentro de `backend-gymapp/` e o primeiro commit também. Agora basta:

```bash
cd ~/Desktop/gym/backend-gymapp

# Crie um repo vazio no GitHub (sem README, sem .gitignore — eu já gerei).
# Depois conecte e faça push:
git remote add origin git@github.com:<seu-user>/<seu-repo>.git
git push -u origin main
```

> **Importante:** `.env` e `.env.prod` estão no `.gitignore` — não vão pro repositório. As credenciais de produção ficam só na VPS.
> O app Swift (pasta `gym/`) ficou de fora deste repo (já tem o próprio `.git` do Xcode). Pra manter os dois sincronizados, você pode tratá-los como repos separados ou criar um monorepo depois.

---

## 2) Bootstrap da VPS (1ª vez, como root)

> **VPS HostGator usa porta SSH 22022** (não a 22 padrão). Se for outro provedor, descubra com `grep ^Port /etc/ssh/sshd_config` na VPS. O bootstrap detecta isso automaticamente.

Pra simplificar SSH, adicione um alias em `~/.ssh/config` no seu Mac:

```bash
cat >> ~/.ssh/config << 'EOF'

Host gym-vps
    HostName <ip-da-vps>
    Port 22022
    User root
EOF
chmod 600 ~/.ssh/config
```

Aí logue e rode:

```bash
ssh gym-vps

# Opção A — script via curl direto do GitHub
curl -fsSL https://raw.githubusercontent.com/<seu-user>/<seu-repo>/main/deploy/scripts/bootstrap-vps.sh | bash

# Opção B — clone primeiro e rode local
apt-get update && apt-get install -y git
git clone https://github.com/<seu-user>/<seu-repo>.git /opt/backend-gymapp
bash /opt/backend-gymapp/deploy/scripts/bootstrap-vps.sh
```

O que esse script faz:
- Instala Docker + Compose plugin
- Detecta a porta SSH ativa e libera **ela** + 80 + 443 no UFW (firewall)
- Cria usuário `deploy` (sem senha, login só por SSH key — copia as chaves do root)

Depois disso, **logue como `deploy`** (na mesma porta):

```bash
ssh -p 22022 deploy@<ip-da-vps>
# OU adicione um segundo alias no ~/.ssh/config:
#   Host gym-deploy
#       HostName <ip-da-vps>
#       Port 22022
#       User deploy
# E aí:  ssh gym-deploy
```

---

## 3) Clonar o projeto e configurar ambiente

```bash
git clone https://github.com/<seu-user>/<seu-repo>.git ~/backend-gymapp
cd ~/backend-gymapp

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

## 6) (Opcional) Frontend Coach — SPA do personal trainer

O `frontend-coach/` é um SPA React/Vite separado do backend, hospedado em `https://coach.seudominio.com.br`. O nginx desta VPS serve o build estático e o backend libera CORS pro domínio.

### 6.1) DNS

Aponte um `A` (ou `CNAME`) de `coach.seudominio.com.br` pro mesmo IP da VPS. Espere o DNS propagar:

```bash
dig +short coach.seudominio.com.br   # deve retornar o IP da VPS
```

### 6.2) Variáveis no `.env.prod` (na VPS)

Confirme/adicione:

```
COACH_DOMAIN=coach.seudominio.com.br
DJANGO_CORS_ALLOWED_ORIGINS=https://coach.seudominio.com.br
```

E reaplique o compose pra o nginx pegar o novo template:

```bash
$COMPOSE up -d
```

### 6.3) Emite o cert TLS pro subdomínio

```bash
bash deploy/scripts/init-coach.sh
```

Idempotente; idêntico ao `init-letsencrypt.sh` mas pra `COACH_DOMAIN`.

### 6.4) Build + publish (do Mac local, não da VPS)

A VPS fica enxuta sem Node — o build é feito no Mac e o `dist/` é rsyncado pro volume `coach_dist` do nginx:

```bash
# No Mac, com Node 20+ instalado e ssh-alias da VPS configurado:
cd backend-gymapp
bash deploy/scripts/deploy-coach.sh gym-deploy
```

O script:
1. `npm install` no `frontend-coach/` (se necessário)
2. `npm run build` (Vite usa `.env.production` automaticamente — aponta pra `https://api.seudominio.com.br`)
3. `rsync` do `dist/` pra `/tmp/coach-dist/` na VPS
4. Copia pro volume `coach_dist` via container alpine (volume é read-only no nginx)

Pronto. Acesse `https://coach.seudominio.com.br`.

---

## 7) Preservar o banco em deploys (IMPORTANTE)

O volume Docker `pgdata` fica preservado em qualquer `docker compose up -d` ou `restart` — só é apagado se rodar `docker compose down -v` (com `-v`). Os scripts deste projeto **nunca** usam `-v`, então em uso normal os dados ficam.

Mesmo assim, antes de qualquer deploy faça **backup explícito**:

```bash
ssh deploy@<ip-da-vps>
cd ~/backend-gymapp
bash deploy/scripts/backup-db.sh   # gera backups/gym-YYYY-MM-DD-HHMM.sql.gz
```

Restaurar (em caso de emergência):

```bash
gunzip -c backups/gym-2026-05-08-1700.sql.gz | \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    --env-file .env.prod exec -T db psql -U gym gym
```

### Risco específico em deploys com migrations novas

Se o seu deploy traz arquivos de migration que já existiam apenas na VPS (gerados localmente lá com `makemigrations`), pode haver colisão no `git pull`:

```
error: The following untracked working tree files would be overwritten by merge:
  accounts/migrations/0002_*.py
```

**Antes do `git pull`**, verifique se o conteúdo é idêntico:

```bash
# Na VPS, na pasta do projeto:
md5sum accounts/migrations/0002_alter_user_options_user_created_by_and_more.py \
       workouts/migrations/0001_initial.py \
       training_sessions/migrations/0001_initial.py 2>/dev/null
```

Compare com o que está no commit local. Se forem idênticos, mova as locais pra fora antes de pullar:

```bash
mkdir -p /tmp/migrations-vps-backup
mv accounts/migrations/0002_alter_user_options_user_created_by_and_more.py /tmp/migrations-vps-backup/ 2>/dev/null || true
mv workouts/migrations/0001_initial.py /tmp/migrations-vps-backup/ 2>/dev/null || true
mv training_sessions/migrations/0001_initial.py /tmp/migrations-vps-backup/ 2>/dev/null || true
git pull --ff-only
# Verifica que o que veio do git é idêntico ao backup
diff /tmp/migrations-vps-backup/0002_alter_user_options_user_created_by_and_more.py \
     accounts/migrations/0002_alter_user_options_user_created_by_and_more.py
```

Como o `django_migrations` na tabela do Postgres já tem essas 3 entries (estavam aplicadas), o `migrate` do `deploy.sh` vai detectar como **already applied** e não vai re-rodar nada. Os dados ficam intactos.

---

## 8) Atualizações futuras (deploy contínuo)

### Backend

A cada push no `main`:

```bash
ssh deploy@<ip-da-vps>
cd ~/backend-gymapp
bash deploy/scripts/backup-db.sh    # SEMPRE antes de deploy
bash deploy/scripts/deploy.sh
```

O `deploy.sh` faz:
- `git pull`
- Rebuild da imagem do `web`
- Restart dos containers
- `migrate` + `collectstatic`
- Reload do nginx

### Frontend coach

A cada mudança no `frontend-coach/` (no Mac):

```bash
cd backend-gymapp
bash deploy/scripts/deploy-coach.sh gym-deploy
```

(Não toca no backend — só atualiza os arquivos estáticos servidos pelo nginx.)

---

## Troubleshooting

**`init-letsencrypt.sh` (ou `init-coach.sh`) falha em "Failed authorization procedure"**
DNS ainda não propagou. Confirme com `dig +short <dominio>` e espere até o IP retornar correto antes de rodar de novo.

**Coach SPA mostra "Erro de conexão" no login**
CORS do backend não inclui o domínio do coach, ou o `COACH_DOMAIN` no `.env.prod` da VPS está desalinhado do que está em `frontend-coach/.env.production`. Confirme:
```bash
# Na VPS:
grep -E "DJANGO_CORS_ALLOWED_ORIGINS|COACH_DOMAIN" .env.prod
$COMPOSE restart web nginx
```

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
