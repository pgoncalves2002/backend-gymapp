# Imagem leve com Python 3.12
FROM python:3.12-slim

# Não bufferizar stdout/stderr e não criar .pyc
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependências de sistema — libpq pro psycopg, build tools removidos depois
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar deps Python primeiro pra aproveitar cache do Docker
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar o código
COPY . .

EXPOSE 8000

# CMD padrão = produção (gunicorn).
# Em dev, docker-compose sobrescreve com `python manage.py runserver`.
CMD ["gunicorn", "gym_api.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
