"""Конфигурация приложения.

Все настройки читаются из переменных окружения (или файла ``.env``) через
Pydantic Settings. Единая точка доступа — функция :func:`get_settings`
(кешируется, поэтому объект настроек создаётся один раз за процесс).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Типизированные настройки сервиса."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Приложение ──────────────────────────────────────────────────────────
    app_env: Literal["local", "staging", "production"] = "local"
    app_debug: bool = True
    api_v1_prefix: str = "/api/v1"
    # Префикс, под которым сервис висит за обратным прокси (например ``/skrz``).
    # Маршрутизацию НЕ меняет — прокси срезает префикс сам; влияет на ссылки,
    # которые генерирует FastAPI: без него Swagger/ReDoc просят /openapi.json по
    # абсолютному пути и попадают в соседний сервис за тем же доменом.
    root_path: str = ""
    # NoDecode — отключаем встроенный JSON-парсинг источника; строку
    # «домен1,домен2» разбираем сами в валидаторе ниже.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── PostgreSQL / Redis ──────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://skrezo:skrezo@localhost:5432/skrezo"
    redis_url: str = "redis://localhost:6379/0"

    # ── JWT: единый контур авторизации ──────────────────────────────────────
    # Один секрет на всех (команда Skrezo и сотрудники клиник входят одинаково —
    # телефон + пароль). В production обязательно переопределить длинным
    # случайным значением (≥32 байт для HS256): `openssl rand -hex 32`.
    jwt_secret: str = "dev-only-change-me-jwt-secret-32-bytes-minimum"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    # ── Rate-limit (защита от перебора; окна фиксированные, счётчики в Redis) ──
    login_rate_limit: int = 10  # попыток входа
    login_rate_window_sec: int = 60  # …за окно, на пару IP+телефон
    lead_rate_limit: int = 5  # заявок с одного IP
    lead_rate_window_sec: int = 3600  # …за час
    change_password_rate_limit: int = 10
    change_password_rate_window_sec: int = 60
    otp_request_rate_limit: int = 5  # запросов кода на пару IP+телефон
    otp_request_rate_window_sec: int = 300  # …за 5 минут

    # ── OTP-вход соло-врачей ────────────────────────────────────────────────
    otp_length: int = 6
    otp_ttl_seconds: int = 300  # срок жизни кода
    otp_max_attempts: int = 5  # неверных попыток на код до блокировки
    otp_registration_ttl_seconds: int = 900  # срок жизни registrationToken после verify
    # true — вернуть код в ответе /auth/otp/request (dev/тесты, канал доставки не выбран).
    otp_expose_code: bool = False

    # ── Доставка OTP: каналы в порядке приоритета ───────────────────────────
    # «telegram,whatsapp,sms» — сначала бесплатный Telegram Gateway, при неудаче
    # WhatsApp, и только потом платная SMS. Пусто (по умолчанию) — код пишется в
    # лог: так живут локальная разработка и тесты, в прод так выпускать нельзя.
    otp_channels: Annotated[list[str], NoDecode] = Field(default_factory=list)
    otp_delivery_timeout_seconds: float = 5.0
    # Telegram Gateway (gatewayapi.telegram.org): код показывается своим шаблоном.
    telegram_gateway_url: str = "https://gatewayapi.telegram.org"
    telegram_gateway_token: str = ""
    # WhatsApp-бот: номер уходит без ведущего плюса.
    whatsapp_api_url: str = ""
    whatsapp_account_id: str = ""
    # SMSPRO (nikita.kg): XML-протокол, отправитель согласуется с оператором.
    smspro_url: str = "https://smspro.nikita.kg/api/message"
    smspro_login: str = ""
    smspro_password: str = ""
    smspro_sender: str = ""

    # ── Уведомления команды Skrezo ──────────────────────────────────────────
    telegram_team_bot_token: str = ""
    telegram_team_chat_id: str = ""

    # ── Биллинг ─────────────────────────────────────────────────────────────
    trial_days: int = 30
    card_attach_deadline_days: int = 7
    default_lang: str = "ru"

    @field_validator("cors_origins", "otp_channels", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Разрешаем задавать список строкой через запятую."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Вернуть закешированный синглтон настроек."""
    return Settings()


settings = get_settings()
