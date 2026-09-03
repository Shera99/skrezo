# syntax=docker/dockerfile:1

FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# uv — менеджер зависимостей
RUN pip install --no-cache-dir uv

# Слой зависимостей (кешируется отдельно от кода)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project

# Код приложения
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000

# Точка входа — asgi_app: это FastAPI, обёрнутый Socket.IO (Ф4). Запуск
# app.main:app поднимет HTTP без сокета, и реалтайм молча не заработает.
CMD ["uvicorn", "app.main:asgi_app", "--host", "0.0.0.0", "--port", "8000"]
