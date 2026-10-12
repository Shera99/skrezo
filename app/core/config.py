"""Конфигурация приложения.

Все настройки читаются из переменных окружения (или файла ``.env``) через
Pydantic Settings. Единая точка доступа — функция :func:`get_settings`
(кешируется, поэтому объект настроек создаётся один раз за процесс).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

import phonenumbers
from pydantic import Field, ValidationInfo, field_validator, model_validator
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
    #: DSN Postgres МИМО пулера. PgBouncer в transaction-режиме не переживает
    #: ``LISTEN`` (шина realtime, будильник диспетчера) и не нужен миграциям —
    #: они ходят сюда. Пусто — берётся ``DATABASE_URL`` (без пулера они равны).
    database_direct_url: str = ""

    # ── Шифрование секретов в базе ──────────────────────────────────────────
    #: Ключ Fernet для секретов, которые приходится хранить в БД (токены Meta
    #: клиник из Embedded Signup). Несколько ключей через запятую — ротация:
    #: шифрует первый, расшифровывают все. Сгенерировать —
    #: ``Fernet.generate_key()`` из ``cryptography.fernet`` (см. docs/DEPLOY.md).
    #: Пусто — подключение номеров самой клиникой недоступно.
    data_encryption_key: str = ""

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
    # ── Номер для проверки приложения в App Store / Google Play ─────────────
    # Ревьюер стора входит по OTP, но SIM-карты с этим номером у него нет: для
    # одного номера код фиксирован и никуда не отправляется. ВРЕМЕННО — после
    # прохождения проверки обе переменные очистить. Пусто — режим выключен.
    # Задаются только парой; номер приводится к E.164 при старте.
    otp_review_phone: str = ""
    otp_review_code: str = ""

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

    # ── Уведомления пользователям (не OTP) ──────────────────────────────────
    # «whatsapp,sms» — те же провайдеры, что у OTP, но с произвольным текстом:
    # Telegram Gateway сюда не годится, он умеет только коды своим шаблоном.
    # Пусто (по умолчанию) — сообщение пишется в лог (dev и тесты).
    notify_channels: Annotated[list[str], NoDecode] = Field(default_factory=list)
    notify_delivery_timeout_seconds: float = 5.0

    # ── Уведомления команды Skrezo ──────────────────────────────────────────
    telegram_team_bot_token: str = ""
    telegram_team_chat_id: str = ""

    # ── Пуш-уведомления (FCM + APNs) ─────────────────────────────────────────
    # Кредов пока нет: при пустом конфиге транспорт (следующий инкремент)
    # работает через лог-фолбэк, ничего не падает. Включение — заполнением
    # переменных ниже, без правки кода.
    push_enabled: bool = False
    # FCM HTTP v1: JSON service-account (сам JSON или путь к файлу — решает
    # транспорт при чтении настройки).
    fcm_service_account_json: str = ""
    # APNs token-based auth: содержимое .p8-ключа (или путь к файлу) + идентификаторы.
    apns_key: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    # true — слать в sandbox APNs (dev-сборки приложения); false — прод.
    apns_use_sandbox: bool = True

    # ── WhatsApp Cloud API (переписка с пациентами; НЕ каналы OTP выше) ─────
    # Пустой access token включает лог-фолбэк: входящие сохраняются, исходящие
    # пишутся в лог — система работает без кредов Meta (как пуши без FCM/APNs).
    wa_cloud_api_url: str = "https://graph.facebook.com/v23.0"
    wa_cloud_access_token: str = ""  # system-user token приложения Meta
    wa_cloud_app_secret: str = ""  # APP_SECRET — подпись вебхука X-Hub-Signature-256
    wa_cloud_verify_token: str = ""  # проверочный токен подписки вебхука (GET)
    #: Самостоятельное подключение номера клиникой (Embedded Signup Meta): ID
    #: приложения Meta и ID конфигурации «Facebook Login for Business» с типом
    #: WhatsApp Embedded Signup. Оба пусты — кнопки подключения в кабинете нет,
    #: номера привязывает команда Skrezo вручную (``/admin/clients/{id}/whatsapp``).
    wa_cloud_app_id: str = ""
    wa_embedded_signup_config_id: str = ""
    #: Служебные шаблоны Meta (тестовый ``hello_world`` и подобные): в каталог
    #: клиники не попадают и пациентам не отправляются. Список через запятую.
    wa_service_template_names: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["hello_world"]
    )
    #: Напоминание о визите (Ф7): имя одобренного шаблона в Meta (общий шаблон
    #: платформы или свой у клиники), переменные по порядку — имя, дата, время,
    #: клиника (сколько ждёт шаблон); две quick-reply-кнопки «Приду»/«Перенести».
    wa_visit_reminder_template: str = "visit_reminder"
    wa_visit_reminder_buttons: bool = True
    #: За сколько часов напоминать и за сколько считать визит неподтверждённым.
    visit_reminder_hours_before: int = 24
    visit_unconfirmed_hours_before: int = 3
    #: Напоминание о долге (Ф7): шаблон (переменные — имя, сумма в сомах, клиника)
    #: и через сколько дней после приёма напоминать об остатке.
    wa_debt_reminder_template: str = "debt_reminder"
    debt_reminder_days_after: int = 3
    #: Повторный осмотр (Ф7): шаблон (имя, месяцев, клиника) с кнопками
    #: «Записаться» / «Не сейчас» и срок с последнего визита в месяцах.
    wa_recall_reminder_template: str = "recall_reminder"
    wa_recall_buttons: bool = True
    recall_months: int = 6

    # ── AI-ассистент (Ф2) ────────────────────────────────────────────────────
    # Провайдер за интерфейсом AiProvider: смена — переменной окружения.
    # "off" (или пустой ключ) выключает ассистента: диалоги копятся, отвечает
    # человек. ⚠️ Бесплатный тир Gemini может использовать содержимое запросов
    # для улучшения продуктов Google — до реальных пациентов включить платный.
    ai_provider: str = "off"  # off | gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    # Уровень размышлений Gemini: у фактических ответов мысли не нужны (валидатор
    # проверяет и так), а free-tier токены и латентность — нужны. Пусто — дефолт модели.
    gemini_thinking_level: str = "minimal"
    # Фактические ответы: низкая температура + короткие ответы (§4.3, §5).
    ai_temperature: float = 0.2
    ai_max_tokens: int = 1024
    # Потолок вызовов инструментов на один ход — защита от зацикливания модели.
    ai_max_tool_calls: int = 6
    #: §14: сколько сообщений в час принимаем от одного пациента. При
    #: превышении бот один раз извиняется и передаёт диалог человеку.
    ai_rate_limit_per_hour: int = 30
    #: §8.4: через сколько минут без ответа сотрудника диалог, переданный
    #: администратору, возвращается боту (решение 12.10.2026). 0 — не возвращать.
    #: «Отвечаю сам» и неотложное состояние не возвращаются никогда.
    ai_escalation_return_minutes: int = 15
    #: Ассистент не предлагает время раньше, чем через столько минут: в 14:59
    #: «сегодня в 15:00» пациенту уже не успеть. Касается только подбора
    #: ботом, сетка кабинета прежняя.
    ai_slot_lead_minutes: int = 30
    #: §11: распознавание голосовых. Пусто — выключено, голосовые уходят
    #: администратору; "gemini" — расшифровка тем же ключом, что и ассистент.
    speech_provider: str = ""
    speech_model: str = "gemini-3.6-flash"
    #: Ниже этого порога расшифровке не верим и зовём человека, а не гадаем.
    speech_min_confidence: float = 0.7

    # ── Ф8: нагрузка и надёжность ───────────────────────────────────────────
    #: Пул соединений с БД на процесс. Дефолт SQLAlchemy (5 + 10) давал потолок
    #: ≈15 одновременных ответов ассистента — каждый ждёт модель секунды.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    #: Одновременных заданий в arq-воркере (дефолт arq — 10). Задания ответа
    #: почти всё время ждут модель, поэтому их можно держать десятками.
    worker_max_jobs: int = 25
    #: Пауза перед ответом: «дописал ли пациент?». Несколько сообщений подряд
    #: получают один ответ, а не по ответу на каждое.
    ai_reply_debounce_seconds: int = 3
    #: Таймаут задания ответа: до 7 вызовов модели по 45 с — с запасом.
    ai_reply_job_timeout: int = 360
    #: Отложить ответ, если диалог занят другим заданием или модель временно
    #: недоступна: через сколько секунд и сколько раз, прежде чем звать человека.
    ai_reply_defer_seconds: int = 20
    ai_reply_max_defers: int = 5
    #: Повторы вызова модели на 429/5xx/сеть и база экспоненциального бэкоффа.
    ai_provider_retries: int = 2
    ai_provider_backoff_seconds: float = 1.0
    #: Предохранитель: N сбоев модели подряд размыкают цепь на T секунд —
    #: ответы откладываются вместо лавины эскалаций администраторам.
    ai_breaker_threshold: int = 5
    ai_breaker_open_seconds: int = 60
    #: Кэш префикса промпта у провайдера (Ф8): системный промпт клиники с
    #: инструментами кладётся в cachedContents Gemini и переиспользуется всеми
    #: ходами клиники, пока не изменится. TTL — сколько живёт кэш без правок;
    #: реестр имён в Redis.
    ai_prefix_cache_enabled: bool = True
    ai_prefix_cache_ttl_seconds: int = 1800
    #: Ретеншн истории ассистента (Ф8): журнал инструментов — технический след,
    #: переписка закрытых диалогов — дольше; резюме диалогов не удаляются.
    ai_tool_call_retention_days: int = 90
    ai_message_retention_days: int = 730
    #: Регрессия клиники (Ф6): таймаут задания прогона и потолок кейсов за прогон.
    #: В режиме model каждый кейс — вызов модели (десятки секунд на free tier).
    ai_regression_job_timeout: int = 1800
    ai_regression_max_cases: int = 200
    #: Гейт доверия (Ф7, spec_1): столько одобренных ответов и ноль фактических
    #: ошибок (V01–V03) за столько дней — можно поднимать ступень.
    ai_trust_gate_approvals: int = 200
    ai_trust_gate_fact_error_days: int = 7
    #: Ступень «Проверяю» открывается после стольких ответов, прошедших валидатор
    #: в теневом режиме (цифры теневого режима — §13.1).
    ai_trust_gate_shadow_replies: int = 50

    # ── Учёт использования: прайс-лист для сметы (тыйыны) ───────────────────
    #: Ставки за МИЛЛИОН токенов модели и за одно платное сообщение WhatsApp.
    #: Нули — сметы нет, только счётчики. Правятся, когда меняются тарифы
    #: провайдеров; смета пересчитывается по тем же счётчикам задним числом.
    ai_rate_input_tiyin_per_mtok: int = 0
    ai_rate_output_tiyin_per_mtok: int = 0
    #: Ставка за токены, взятые из кэша префикса (обычно ~четверть входной).
    ai_rate_cached_input_tiyin_per_mtok: int = 0
    wa_rate_utility_tiyin: int = 0
    wa_rate_marketing_tiyin: int = 0
    wa_rate_authentication_tiyin: int = 0

    # ── Веб-кабинет клиники ─────────────────────────────────────────────────
    #: Адрес кабинета — https://office.skrezo.com (без www и слеша в конце).
    #: От него строятся ссылки в сообщениях, возврат со страницы оплаты
    #: (``/pay?payment=<id>``) и домен кода в SMS. Домен привязан к приложению
    #: (universal links), поэтому ссылки только прямые, без редиректов.
    #: Пусто — ссылок нет (локальная разработка).
    cabinet_url: str = ""

    # ── Оплата подписки: FreedomPay (Кыргызстан), платёжная страница ────────
    #: Реквизиты мерчанта из кабинета FreedomPay; пусто — онлайн-оплата
    #: недоступна (ручка отвечает 409, ручная отметка оплаты остаётся).
    freedompay_api_url: str = "https://api.freedompay.kg"
    freedompay_merchant_id: str = ""
    freedompay_secret_key: str = ""
    #: Тестовый режим провайдера (pg_testing_mode): тестовые карты, без денег.
    freedompay_testing_mode: bool = True
    #: Сколько живёт ссылка на оплату (секунды). Вернуть владельца провайдер
    #: должен на ``{CABINET_URL}/pay?payment=<id>`` — адрес строится от кабинета.
    freedompay_lifetime_seconds: int = 1800
    #: Возврат без кнопки (``AUTOGET``) при ``returnMode=auto``. Значение из API
    #: PayBox, нынешняя документация FreedomPay его не перечисляет: отказ
    #: провайдера оплату не ломает (повтор с кнопкой), но стоит лишнего запроса.
    #: Выключить, если провайдер не принимает, — возврат везде кнопкой.
    freedompay_auto_return: bool = True
    #: Публичный адрес API (с префиксом /api/v1) — для серверного колбэка
    #: провайдера, например https://gateway-v2.kassir.kg/skrz/api/v1.
    public_api_url: str = ""
    #: Баланс клиники: минимальное пополнение и за сколько дней до конца
    #: периода автопродление списывает месяц с баланса.
    topup_min_tiyin: int = 100_000
    autopay_days_before: int = 3

    # ── Хранилище файлов ────────────────────────────────────────────────────
    # Каталог на диске сервера (относительно корня проекта или абсолютный).
    # Внутри — storage/{images|files}/ГГГГ/ММ/ДД/. В докере это volume: без него
    # снимки исчезнут при первом же пересоздании контейнера.
    storage_root: str = "storage"
    # Потолок размера загрузки. Считается по мере чтения, а не по Content-Length.
    upload_max_bytes: int = 20 * 1024 * 1024

    # ── Фоновые задачи ──────────────────────────────────────────────────────
    # За сколько дней предупредить владельца о конце пробного периода.
    trial_warning_days: int = 3
    # Сколько хранить журнал запусков джоб: таблица растёт с каждым сообщением.
    job_run_retention_days: int = 30

    # ── Биллинг ─────────────────────────────────────────────────────────────
    trial_days: int = 30
    card_attach_deadline_days: int = 7
    default_lang: str = "ru"

    @field_validator(
        "cors_origins",
        "otp_channels",
        "notify_channels",
        "wa_service_template_names",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Разрешаем задавать список строкой через запятую."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cabinet_url", mode="after")
    @classmethod
    def _check_cabinet_url(cls, value: str, info: ValidationInfo) -> str:
        """Адрес кабинета: схема и хост без пути; в проде — только https и без www.

        Ошибка при старте лучше тихой поломки: адрес без схемы превращал возврат
        с оплаты в «office.skrezo.com/pay» (провайдер такой отвергнет — все
        оплаты в 502), а ``http://`` и ``www`` ломают переход в приложение:
        iOS открывает его только по ссылке прямо на привязанный домен.
        """
        value = value.strip().rstrip("/")
        if not value:
            return value
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "CABINET_URL — адрес вида https://office.skrezo.com: со схемой, без пути"
            )
        if info.data.get("app_env") == "production" and (
            parts.scheme != "https" or parts.hostname.startswith("www.")
        ):
            raise ValueError(
                "CABINET_URL в проде — https и без www: иначе iOS откроет браузер вместо приложения"
            )
        return value

    @field_validator("otp_review_phone", mode="after")
    @classmethod
    def _normalize_review_phone(cls, value: str) -> str:
        """Номер ревью сторов → E.164: с ним сравнивается нормализованный телефон запроса.

        Нормализуем здесь, а не при каждом запросе: опечатка в номере должна
        ронять старт, а не каждый запрос кода у всех пользователей.
        """
        value = value.strip()
        if not value:
            return value
        try:
            parsed = phonenumbers.parse(value, "KG")
        except phonenumbers.NumberParseException as exc:
            raise ValueError("OTP_REVIEW_PHONE — некорректный номер телефона") from exc
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("OTP_REVIEW_PHONE — некорректный номер телефона")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    @model_validator(mode="after")
    def _check_otp_review(self) -> Settings:
        """Номер и код ревью — только парой, код той же длины и из цифр, что обычный.

        Половина пары — почти наверняка недоделанная настройка: номер без кода
        молча ничего не включил бы, а код без номера не к чему привязать.
        """
        code = self.otp_review_code.strip()
        if bool(self.otp_review_phone) != bool(code):
            raise ValueError("OTP_REVIEW_PHONE и OTP_REVIEW_CODE задаются только вместе")
        if code and (not code.isdigit() or len(code) != self.otp_length):
            raise ValueError(f"OTP_REVIEW_CODE — ровно {self.otp_length} цифр")
        self.otp_review_code = code
        return self

    @property
    def direct_database_url(self) -> str:
        """DSN мимо пулера в формате SQLAlchemy: миграции Alembic."""
        return self.database_direct_url or self.database_url

    @property
    def listen_dsn(self) -> str:
        """DSN мимо пулера для ``asyncpg.connect`` (LISTEN): без драйверного префикса."""
        return self.direct_database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Вернуть закешированный синглтон настроек."""
    return Settings()


settings = get_settings()
