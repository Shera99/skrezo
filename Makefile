.PHONY: install up down infra logs migrate revision seed run worker create-admin postman ai-regression lint fmt typecheck test check

install:            ## Установить зависимости (uv)
	uv sync

infra:              ## Поднять только postgres + redis
	docker compose up -d postgres redis

up:                 ## Поднять весь стек в docker
	docker compose up -d --build

down:               ## Остановить стек
	docker compose down

logs:
	docker compose logs -f api worker

migrate:            ## Применить миграции
	uv run alembic upgrade head

revision:           ## Создать пустую ревизию: make revision m="описание"
	uv run alembic revision -m "$(m)"

run:                ## Запустить API локально (reload)
	uv run uvicorn app.main:asgi_app --reload

worker:             ## Запустить arq-воркер локально
	uv run arq app.jobs.worker.WorkerSettings

create-admin:       ## Создать/обновить админа Skrezo: make create-admin phone=0700000001 password=secret
	uv run python -m app.scripts.create_platform_user --phone $(phone) --password $(password)

postman:            ## Сгенерировать Postman-коллекцию из OpenAPI → docs/
	uv run python scripts/gen_postman_collection.py

ai-regression:      ## Регрессия ассистента — ОБЯЗАТЕЛЬНО перед правкой промпта
	uv run python scripts/run_ai_regression.py

lint:
	uv run ruff check app tests

fmt:
	uv run ruff format app tests
	uv run ruff check --fix app tests

typecheck:
	uv run mypy app tests

test:
	uv run pytest

check: lint typecheck test  ## Полная проверка перед коммитом
