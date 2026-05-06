# gym — backend Django

Backend REST para o app iOS `gym`. **Stack:** Django 5 · DRF · PostgreSQL 16 · JWT · Docker.

> **Status:** fase 3 (endpoints REST + JWT). API completa para o app Swift consumir. Próxima fase: integrar o app iOS via `URLSession`.

---

## Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (com Docker Compose v2)
- Nada mais. Python e Postgres rodam dentro dos containers.

---

## Subindo o ambiente local (primeira vez)

```bash
cd backend-django

# 1. Copiar variáveis de ambiente e ajustar se quiser
cp .env.example .env

# 2. Gerar uma SECRET_KEY decente e colocar no .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
# (cole o resultado em DJANGO_SECRET_KEY no .env)

# 3. Build da imagem
docker compose build

# 4. Validar que tudo está coerente (sem aplicar nada no banco ainda)
docker compose run --rm web python manage.py check

# 5. Subir Postgres + Django (Postgres já vem com healthcheck)
docker compose up -d

# 6. Gerar migrations a partir dos models e aplicar no banco
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate

# 7. Criar um superuser pra acessar o admin
docker compose exec web python manage.py createsuperuser
```

Depois disso o admin fica em **http://localhost:8000/admin/**.

---

## Comandos do dia a dia

```bash
# Ver logs (ctrl+c sai sem parar os containers)
docker compose logs -f web

# Abrir um shell Django (orm interativo)
docker compose exec web python manage.py shell

# psql diretamente no banco
docker compose exec db psql -U gym -d gym

# Parar tudo (mantém os dados)
docker compose down

# Parar e APAGAR volume do Postgres (zera o banco)
docker compose down -v
```

---

## Estrutura

```
backend-django/
├── docker-compose.yml         # serviços web + db
├── Dockerfile                 # imagem Python 3.12 + deps
├── requirements.txt           # Django, DRF, simplejwt, psycopg, etc.
├── .env.example               # template das vars (copiar pra .env)
├── manage.py
│
├── gym_api/                   # config do projeto Django
│   ├── settings.py            # lê .env, configura JWT, CORS, Postgres
│   ├── urls.py                # admin/ + (futuro) /api/...
│   ├── wsgi.py
│   └── asgi.py
│
├── accounts/                  # User custom + role student/trainer
│   ├── models.py              # User(AbstractUser)
│   └── admin.py
│
├── workouts/                  # núcleo do domínio
│   ├── models.py              # Workout, Exercise
│   └── admin.py               # com inline de Exercise dentro de Workout
│
├── training_sessions/         # histórico de execuções
│   ├── models.py              # WorkoutSession, ExerciseSetLog
│   └── admin.py
│
├── MODELAGEM.md               # análise do app Swift + decisões + diagrama ER
└── models_preview.py          # histórico da fase 1 (substituído pelos models reais)
```

---

## Modelos persistidos

| Modelo | App | O que representa |
|---|---|---|
| `User` | `accounts` | Aluno ou personal trainer (campo `role`) |
| `Workout` | `workouts` | Ficha de treino — espelha `struct Workout` no Swift |
| `Exercise` | `workouts` | Exercício dentro da ficha — espelha `struct Exercise` |
| `WorkoutSession` | `training_sessions` | Uma execução de uma ficha (timestamps, status) |
| `ExerciseSetLog` | `training_sessions` | Cada série feita (carga real, marcada como concluída) |

Veja `MODELAGEM.md` pro diagrama ER completo e mapeamento campo a campo Swift ↔ Django.

---

## Variáveis de ambiente

Todas no `.env`. As essenciais:

| Variável | Pra que serve |
|---|---|
| `DJANGO_SECRET_KEY` | Assinatura de sessões/JWT. **Deve ser aleatória e única.** |
| `DJANGO_DEBUG` | `True` em dev, `False` em prod. |
| `DJANGO_ALLOWED_HOSTS` | Lista separada por vírgula. Em prod, o domínio da VPS. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Compartilhados entre `db` e `web`. |
| `JWT_ACCESS_LIFETIME_MIN` | Tempo de vida do access token (default: 60min). |
| `JWT_REFRESH_LIFETIME_DAYS` | Tempo de vida do refresh token (default: 14 dias). |

---

## Endpoints da API

Base URL local: `http://localhost:8000`. Todos os endpoints exceto `register/` e `login/` exigem header `Authorization: Bearer <access_token>`.

### Auth

| Método | Path | Permissão | Descrição |
|---|---|---|---|
| `POST` | `/api/auth/register/` | público | Cria um aluno (sempre `role=student`) |
| `POST` | `/api/auth/login/` | público | Devolve `access`, `refresh` e `user` |
| `POST` | `/api/auth/refresh/` | público | Troca `refresh` por novo `access` |
| `GET` | `/api/auth/me/` | logado | Dados do user logado |
| `PATCH` | `/api/auth/me/` | logado | Edita `display_name`, `email`, `birth_date` |

### Sync (one-shot pro app offline)

| Método | Path | Permissão | Descrição |
|---|---|---|---|
| `GET` | `/api/sync/` | logado | Devolve `{user, workouts:[com exercises aninhados], synced_at}` numa única request. Otimizado com prefetch — usado pelo app Swift no botão de sincronizar. |

### Workouts (fichas)

| Método | Path | Permissão | Descrição |
|---|---|---|---|
| `GET` | `/api/workouts/` | aluno ou trainer | Lista fichas (do aluno OU criadas pelo trainer) |
| `POST` | `/api/workouts/` | trainer | Cria ficha pra um aluno (campo `student`) |
| `GET` | `/api/workouts/{id}/` | aluno dono ou trainer criador | Detalhe + `exercises` aninhados |
| `PATCH` | `/api/workouts/{id}/` | trainer criador | Edita ficha |
| `DELETE` | `/api/workouts/{id}/` | trainer criador | Remove ficha (e exercícios) |

### Exercises (exercícios das fichas)

| Método | Path | Permissão | Descrição |
|---|---|---|---|
| `GET` | `/api/exercises/` | logado | Lista exercícios visíveis pro user |
| `POST` | `/api/exercises/` | trainer | Cria exercício numa ficha |
| `PATCH` | `/api/exercises/{id}/` | trainer da ficha | Edita exercício (inclui upload de `demo_gif`) |
| `DELETE` | `/api/exercises/{id}/` | trainer da ficha | Remove exercício |

> **Upload do GIF de demonstração (`demo_gif`)** — o campo é um `FileField`.
> Na criação/edição use `Content-Type: multipart/form-data`. Limites: extensão `.gif`,
> tamanho máximo 20 MB. Arquivos ficam em `media/exercises/<workout_id>/<exercise_id>.gif`
> e são servidos em `http://localhost:8000/media/...` durante o desenvolvimento
> (em produção quem serve é o nginx — fase 5). No GET o campo aparece como URL
> absoluta pronta pro app baixar.

### Sessions (execuções de treino) e Set Logs (séries)

| Método | Path | Permissão | Descrição |
|---|---|---|---|
| `GET` | `/api/sessions/` | aluno | Histórico de execuções do aluno |
| `POST` | `/api/sessions/` | aluno | **Inicia sessão**: cria `WorkoutSession` + auto-cria todos os `ExerciseSetLog` da ficha |
| `GET` | `/api/sessions/{id}/` | aluno dono | Detalhe + `set_logs` aninhados |
| `PATCH` | `/api/sessions/{id}/` | aluno dono | Atualiza `elapsed_seconds`, `status`. Status terminal seta `finished_at` automaticamente |
| `DELETE` | `/api/sessions/{id}/` | aluno dono | Apaga sessão (e set_logs) |
| `PATCH` | `/api/set-logs/{id}/` | aluno dono | Atualiza `load_kg` ou `is_completed` de uma série. Marcar como concluída seta `completed_at` automaticamente |

### Exemplos curl

```bash
# 1. Registrar aluno
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username":"pedro","email":"pedro@example.com","password":"senha123ABC","display_name":"Pedro"}'

# 2. Login (devolve access + refresh + user)
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"pedro","password":"senha123ABC"}'

# 3. Listar fichas (precisa do access token)
curl http://localhost:8000/api/workouts/ \
  -H "Authorization: Bearer <ACCESS_TOKEN>"

# 4. Iniciar uma sessão (cria sessão + todas as set_logs)
curl -X POST http://localhost:8000/api/sessions/ \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"workout":"<workout-uuid>"}'

# 5. Marcar uma série como concluída (com carga real)
curl -X PATCH http://localhost:8000/api/set-logs/<set-log-uuid>/ \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"load_kg":42.5,"is_completed":true}'

# 6. Finalizar sessão
curl -X PATCH http://localhost:8000/api/sessions/<session-uuid>/ \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"status":"completed","elapsed_seconds":2880}'

# 7. Upload do GIF de demonstração de um exercício (multipart, sem -d)
curl -X PATCH http://localhost:8000/api/exercises/<exercise-uuid>/ \
  -H "Authorization: Bearer <TRAINER_ACCESS_TOKEN>" \
  -F "demo_gif=@/caminho/para/supino.gif"
```

### Regras de permissão (resumo)

- **Aluno (`role=student`)** — vê só as próprias fichas e sessões. Pode iniciar sessões, registrar séries e ajustar carga real, mas não edita a ficha em si.
- **Trainer (`role=trainer`)** — cria/edita fichas e exercícios para os seus alunos. **Não** acessa as sessões executadas (são privadas do aluno).
- **Admin** — acesso total via `/admin/`.

---

## Próximas fases

- **Fase 4** — adaptar o app Swift: `APIClient` com `URLSession`, `AuthStore` segurando o JWT no Keychain, substituir `SampleData` por chamadas reais à API.
- **Fase 5** — preparar pra produção: `docker-compose.prod.yml` com nginx + certbot, script de deploy pra VPS, GitHub Actions opcional.

---

## Troubleshooting rápido

**`docker compose up` reclama de porta 5432 ocupada:** você tem Postgres local rodando. Pare-o (`brew services stop postgresql`) ou mude a porta no `docker-compose.yml` (ex.: `"5433:5432"`).

**`relation "..." does not exist` ao acessar o admin:** faltou rodar `migrate`. Volte ao passo 6 do setup.

**Mudanças nos `models.py` não aparecem:** rode `makemigrations` (gera o arquivo de migration) **e** `migrate` (aplica no banco). O auto-reload do `runserver` só recarrega código Python — schema do banco não muda sozinho.

**Esqueceu a senha do superuser:** `docker compose exec web python manage.py changepassword <username>`.
