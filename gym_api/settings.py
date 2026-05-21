"""
Django settings for gym_api project.

Lê configuração de variáveis de ambiente via django-environ.
Em dev: docker-compose injeta o .env. Em prod: VPS injeta as vars.
"""

import os
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-not-for-prod")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# CSRF_TRUSTED_ORIGINS — em produção, nginx serve em https://dominio e
# repassa pro Django. Sem este setting, o admin retorna 403 em POSTs.
# Aceita lista de origens completas (com scheme): "https://api.dominio.com.br".
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Atrás de um reverse proxy (nginx) que termina TLS:
# - confia no header X-Forwarded-Proto pra saber se a request original foi HTTPS
# - usa o Host enviado pelo nginx
# Em dev (sem nginx) essas configs ficam inertes.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Headers de segurança extras quando NÃO é dev.
if not DEBUG:
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # 30 dias (após estável, aumentar)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 3rd party
    "rest_framework",
    "rest_framework_simplejwt",
    # Blacklist token (usado pelo ActiveUserTokenRefreshView pra invalidar
    # refresh tokens de alunos bloqueados imediatamente).
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    # local
    "accounts",
    "workouts",
    "training_sessions",
    "billing",
    "payments",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # antes de CommonMiddleware
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gym_api.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "gym_api.wsgi.application"
ASGI_APPLICATION = "gym_api.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", default="db"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
# User custom (com role student/trainer) — definido em accounts.User
AUTH_USER_MODEL = "accounts.User"

# TEMPORÁRIO — sem validação de senha pra facilitar testes em dev.
# ANTES DE PRODUÇÃO REAL, restaurar pra:
#     {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
#     {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
#     {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
#     {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
AUTH_PASSWORD_VALIDATORS: list = []

# ---------------------------------------------------------------------------
# DRF + JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # DecimalField como número JSON (não string) — compat com Swift Decimal.
    "COERCE_DECIMAL_TO_STRING": False,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_LIFETIME_MIN", default=60)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=env.int("JWT_REFRESH_LIFETIME_DAYS", default=14)
    ),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])
# App iOS chama via URLSession (sem CORS). Em dev, libera localhost pra testes web.

# ---------------------------------------------------------------------------
# Billing / Asaas (assinatura do personal pelo uso do app + split dos alunos)
# ---------------------------------------------------------------------------
# Modo scaffold: enquanto ASAAS_API_KEY estiver vazia, o app sobe normal e
# o fluxo GRÁTIS funciona 100%; só os endpoints que falam com o Asaas
# respondem 503 com mensagem clara. Pluga as chaves de sandbox e reinicia.
#
# NOTA: usamos `os.environ.get` direto (não `env()`) porque a chave do Asaas
# começa com `$` (ex.: `$aact_...`) e o django-environ chama
# `os.path.expandvars` que tentaria interpretar isso como variável shell e
# devolveria vazio. `os.environ.get` entrega o valor literal.
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "")
ASAAS_API_BASE = env("ASAAS_API_BASE", default="https://sandbox.asaas.com/api/v3")
# Shared secret enviado pelo Asaas no header `asaas-access-token`.
ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN", "")
# Valor (em centavos) por plano da assinatura do personal. Definido no servidor
# pra ninguém forjar o preço pelo front.
ASAAS_PRICES = {
    "monthly": env.int("ASAAS_PRICE_MONTHLY_CENTS", default=0),
    "annual": env.int("ASAAS_PRICE_ANNUAL_CENTS", default=0),
}
# Plano grátis: quantos alunos um personal sem assinatura paga pode ter.
FREE_STUDENT_LIMIT = env.int("FREE_STUDENT_LIMIT", default=1)

# ---------------------------------------------------------------------------
# Split (personal cobra os alunos; FichaGym fica com %)
# ---------------------------------------------------------------------------
# Percentual que a FichaGym fica em cada transação do aluno pro personal.
# Sai ANTES do rateio (não recai 100% na plataforma). Default 5%.
PLATFORM_FEE_PERCENT = env.float("PLATFORM_FEE_PERCENT", default=5.0)
# Wallet ID da plataforma (FichaGym) — só usado se quisermos explicitar
# o destino da fee em algum cenário. Default: vazio, o Asaas debita da master.
PLATFORM_WALLET_ID = env("PLATFORM_WALLET_ID", default="")

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / Media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging — joga tracebacks de 500 no stdout (visíveis em `docker compose logs`)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        # Em prod, django.request com nível ERROR loga 500 com traceback completo.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
