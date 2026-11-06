# Skrezo API

Бэкенд платформы для стоматологических клиник: пациенты и карта зубов, расписание,
приёмы, касса, отчёты, подписки и ассистент в WhatsApp.

Python 3.14, FastAPI, PostgreSQL, Redis, Arq.

## Запуск

Нужен Docker.

```bash
cp .env.example .env
make up
```

API: <http://localhost:8010>, Swagger: <http://localhost:8010/docs>.

## Проверки

Нужны [uv](https://docs.astral.sh/uv/) и Docker.

```bash
make check
```

## Документация

- [Разработка](docs/DEVELOPMENT.md)
- [Настройки](docs/CONFIGURATION.md)
- [API](docs/API.md)
- [Фоновые задачи](docs/OPERATIONS.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [База данных](docs/DATABASE.md)
- [Деплой](docs/DEPLOY.md)
