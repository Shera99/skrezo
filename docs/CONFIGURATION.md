# Настройки

Переменные окружения. Шаблон — [`.env.example`](../.env.example).

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `APP_ENV` | `local` / `staging` / `production` | `local` |
| `APP_DEBUG` | подробные логи, echo SQL | `true` |
| `API_V1_PREFIX` | префикс версии API | `/api/v1` |
| `ROOT_PATH` | префикс за обратным прокси (например `/skrz`) | — (пусто) |
| `CORS_ORIGINS` | разрешённые домены (через запятую) | — |
| `DATABASE_URL` | DSN PostgreSQL (asyncpg) | `postgresql+asyncpg://skrezo:skrezo@localhost:5434/skrezo` |
| `DATABASE_DIRECT_URL` | DSN мимо пулера — `LISTEN/NOTIFY` и миграции (прод с PgBouncer) | — (= `DATABASE_URL`) |
| `REDIS_URL` | DSN Redis | `redis://localhost:6380/0` |
| `JWT_SECRET` | секрет единого контура токенов | — |
| `ACCESS_TOKEN_TTL_SECONDS` | срок жизни access-токена | `900` |
| `REFRESH_TOKEN_TTL_SECONDS` | срок жизни refresh-токена | `2592000` |
| `TELEGRAM_TEAM_BOT_TOKEN` / `TELEGRAM_TEAM_CHAT_ID` | карточки команде в Telegram-канал: заявки, регистрации соло-врачей, конец триала (см. [«Уведомления команды»](OPERATIONS.md#уведомления-команды-в-telegram)); ID канала — `-100…` или `@username` | — (в лог) |
| `OTP_CHANNELS` | каналы доставки кода по приоритету: `telegram,whatsapp,sms` | — (лог) |
| `OTP_REVIEW_PHONE` / `OTP_REVIEW_CODE` | **временно**, на время проверки приложения в App Store / Google Play: для одного номера код фиксирован и не отправляется (у ревьюера нет SIM). Только парой, код длины `OTP_LENGTH`; каждый выпуск — `otp_review_code_issued` в логе. После проверки — очистить | — (выключено) |
| `OTP_DELIVERY_TIMEOUT_SECONDS` | таймаут запроса к провайдеру | `5` |
| `TELEGRAM_GATEWAY_URL` / `TELEGRAM_GATEWAY_TOKEN` | Telegram Gateway (код своим шаблоном) | `https://gatewayapi.telegram.org` / — |
| `WHATSAPP_API_URL` / `WHATSAPP_ACCOUNT_ID` | WhatsApp-шлюз wa-service (номер без «+»): `http://127.0.0.1:5001/api/messages/send` и ID аккаунта из `deploy/wa/pair.html` — установка в [DEPLOY.md, раздел 13](DEPLOY.md#13-whatsapp-шлюз-для-otp-wa-service) | — |
| `SMSPRO_URL` / `SMSPRO_LOGIN` / `SMSPRO_PASSWORD` / `SMSPRO_SENDER` | SMS через nikita.kg (XML) | `https://smspro.nikita.kg/api/message` / — |
| `NOTIFY_CHANNELS` | каналы уведомлений (не OTP) по приоритету: `whatsapp,sms` | — (лог) |
| `NOTIFY_DELIVERY_TIMEOUT_SECONDS` | таймаут запроса к провайдеру уведомлений | `5` |
| `PUSH_ENABLED` | рубильник живых пушей; `false` — всё в лог даже с кредами. Проверка доставки — `make push-test phone=…` | `false` |
| `FCM_SERVICE_ACCOUNT_JSON` | пуши Android: service-account Firebase — JSON строкой **или путь к файлу** | — (лог) |
| `APNS_KEY` / `APNS_KEY_ID` / `APNS_TEAM_ID` / `APNS_BUNDLE_ID` / `APNS_USE_SANDBOX` | пуши iOS: ключ `.p8` — содержимое **или путь к файлу** + идентификаторы | — (лог) / `true` |
| `WA_CLOUD_API_URL` | Graph API Meta (переписка WhatsApp, НЕ OTP-канал) | `https://graph.facebook.com/v23.0` |
| `WA_CLOUD_ACCESS_TOKEN` | system-user token приложения Meta | — (лог: исходящие в лог) |
| `WA_CLOUD_APP_SECRET` | подпись вебхука `X-Hub-Signature-256` | — (⚠️ в бою обязателен) |
| `WA_CLOUD_VERIFY_TOKEN` | подтверждение подписки вебхука (GET) | — |
| `WA_CLOUD_APP_ID` | ID приложения Meta — окно подключения номера в кабинете (Embedded Signup) | — |
| `WA_EMBEDDED_SIGNUP_CONFIG_ID` | ID конфигурации Facebook Login for Business (WhatsApp Embedded Signup) | — |
| `DATA_ENCRYPTION_KEY` | ключ Fernet для секретов в БД (токены Meta клиник); несколько через запятую — ротация, шифрует первый | — |
| `AI_PROVIDER` | провайдер ассистента: `off` \| `gemini` | `off` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | ключ Google AI Studio и модель | — / `gemini-3.6-flash` |
| `GEMINI_APP_API_KEY` | ключ голосового помощника приложения (`/clinic/llm/generate`); пусто — `GEMINI_API_KEY`, но только при `AI_PROVIDER=gemini` (`off` выключает и помощника). Ключ из отдельного проекта Google разводит квоты с ассистентом пациентов | — |
| `LLM_APP_MODEL_AGENT` / `LLM_APP_MODEL_CLASSIFY` | модели задач `voice_agent` / `voice_classify`; пусто — `GEMINI_MODEL` | — |
| `LLM_APP_TIMEOUT_AGENT_SECONDS` / `LLM_APP_TIMEOUT_CLASSIFY_SECONDS` | таймаут одного обращения | `4` / `6` |
| `LLM_APP_MAX_OUTPUT_TOKENS_AGENT` / `LLM_APP_MAX_OUTPUT_TOKENS_CLASSIFY` | потолок ответа модели | `512` / `128` |
| `LLM_APP_LIMIT_PER_MINUTE` / `LLM_APP_LIMIT_PER_HOUR` | лимит обращений сотрудника | `20` / `150` |
| `LLM_APP_CLINIC_LIMIT_PER_DAY` | лимит обращений клиники в сутки | `1500` |
| `LLM_APP_MAX_BODY_BYTES` | потолок тела запроса | `65536` |
| `AI_TEMPERATURE` / `AI_MAX_TOKENS` / `AI_MAX_TOOL_CALLS` | параметры фактических ответов | `0.2` / `1024` / `6` |
| `AI_RATE_LIMIT_PER_HOUR` | §14: сообщений в час от одного пациента, дальше — человек | `30` |
| `AI_ESCALATION_RETURN_MINUTES` | §8.4: через сколько минут без ответа сотрудника диалог возвращается боту — считается от первого сообщения пациента после передачи; `0` — не возвращать | `15` |
| `AI_SLOT_LEAD_MINUTES` | ассистент не предлагает время раньше, чем через столько минут | `30` |
| `SPEECH_PROVIDER` / `SPEECH_MODEL` / `SPEECH_MIN_CONFIDENCE` | распознавание голосовых (§11); пусто — выключено | — / `gemini-3.6-flash` / `0.7` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | пул соединений с БД на процесс (Ф8) | `10` / `20` |
| `WORKER_MAX_JOBS` | одновременных заданий arq-воркера (Ф8) | `25` |
| `AI_REPLY_DEBOUNCE_SECONDS` | пауза «дописал ли пациент?» перед ответом ассистента | `3` |
| `AI_REPLY_JOB_TIMEOUT` / `AI_REPLY_DEFER_SECONDS` / `AI_REPLY_MAX_DEFERS` | таймаут задания ответа; шаг и число отложек при занятом диалоге или недоступной модели | `360` / `20` / `5` |
| `AI_PROVIDER_RETRIES` / `AI_PROVIDER_BACKOFF_SECONDS` | повторы вызова модели на 429/5xx/сеть и база бэкоффа | `2` / `1.0` |
| `AI_BREAKER_THRESHOLD` / `AI_BREAKER_OPEN_SECONDS` | предохранитель: сбоев подряд → пауза контура ассистента, секунд | `5` / `60` |
| `AI_HEALTH_DOWNGRADE_AFTER` | §13.5: сколько проверок подряд (раз в 5 минут) модель недоступна, прежде чем клиники из `auto` перейдут в «Проверяю» | `3` |
| `AI_HEALTH_ALIVE_SECONDS` | живой успешный вызов модели не старше стольких секунд — проверка здоровья пробу не делает | `600` |
| `AI_UNANSWERED_ALERT_MINUTES` | пациент ждёт ответа клиники (assisted/auto) дольше — алерт в чат команды | `30` |
| `AI_ESCALATION_REMINDER_MINUTES` | передача без ответа сотрудника дольше — одно напоминание владельцу и менеджеру; `0` — нет | `30` |
| `AI_PREFIX_CACHE_ENABLED` / `AI_PREFIX_CACHE_TTL_SECONDS` | кэш префикса промпта у Gemini (cachedContents): включён ли, сколько живёт без правок | `true` / `1800` |
| `AI_TOOL_CALL_RETENTION_DAYS` / `AI_MESSAGE_RETENTION_DAYS` | ретеншн: журнал инструментов; переписка закрытых диалогов с медиа (резюме остаются) | `90` / `730` |
| `FREEDOMPAY_API_URL` / `FREEDOMPAY_MERCHANT_ID` / `FREEDOMPAY_SECRET_KEY` | FreedomPay (Кыргызстан): базовый URL и реквизиты мерчанта; пусто — онлайн-оплата недоступна | `https://api.freedompay.kg` / `` / `` |
| `FREEDOMPAY_TESTING_MODE` / `FREEDOMPAY_LIFETIME_SECONDS` | тестовый режим провайдера; сколько живёт ссылка на оплату | `true` / `1800` |
| `CABINET_URL` | адрес веб-кабинета (прод — `https://office.skrezo.com`): ссылки в сообщениях, возврат со страницы оплаты на `/pay?payment=<id>`, домен кода входа в SMS. Проверяется при старте: схема и хост без пути, в production — только `https` и без `www`; пусто — без ссылок (при настроенном FreedomPay — предупреждение в логе) | `` |
| `FREEDOMPAY_AUTO_RETURN` | возврат без кнопки (`AUTOGET`) при `returnMode=auto`; `false` — возврат везде кнопкой (если провайдер не принимает `AUTOGET`) | `true` |
| `PUBLIC_API_URL` | публичный адрес API с префиксом `/api/v1` — для серверного колбэка провайдера | `` |
| `TOPUP_MIN_TIYIN` / `AUTOPAY_DAYS_BEFORE` | минимальное пополнение баланса; за сколько дней до конца периода или триала списывать месяц с баланса | `100000` / `3` |
| `AI_RATE_CACHED_INPUT_TIYIN_PER_MTOK` | ставка за токены из кэша префикса (≈ четверть входной) | `0` |
| `AI_REGRESSION_JOB_TIMEOUT` / `AI_REGRESSION_MAX_CASES` | регрессия клиники (Ф6): таймаут задания прогона, кейсов за прогон | `1800` / `200` |
| `AI_TRUST_GATE_APPROVALS` / `AI_TRUST_GATE_FACT_ERROR_DAYS` | гейт доверия (Ф7): одобренных ответов и дней без ошибок V01–V03 | `200` / `7` |
| `AI_TRUST_GATE_SHADOW_REPLIES` | ступень «Проверяю»: ответов, прошедших валидатор в теневом режиме | `50` |
| `WA_VISIT_REMINDER_TEMPLATE` / `WA_VISIT_REMINDER_BUTTONS` | напоминание о визите (Ф7): имя шаблона в Meta и есть ли у него две quick-reply-кнопки | `visit_reminder` / `true` |
| `WA_SERVICE_TEMPLATE_NAMES` | служебные шаблоны Meta (через запятую): в каталог клиники не попадают, отправка → 422 `template_service` | `hello_world` |
| `VISIT_REMINDER_HOURS_BEFORE` / `VISIT_UNCONFIRMED_HOURS_BEFORE` | за сколько часов напоминать; за сколько считать визит неподтверждённым | `24` / `3` |
| `WA_DEBT_REMINDER_TEMPLATE` / `DEBT_REMINDER_DAYS_AFTER` | напоминание о долге (Ф7): шаблон и через сколько дней после приёма | `debt_reminder` / `3` |
| `WA_RECALL_REMINDER_TEMPLATE` / `WA_RECALL_BUTTONS` / `RECALL_MONTHS` | повторный осмотр (Ф7): шаблон, кнопки «Записаться»/«Не сейчас», месяцев после визита | `recall_reminder` / `true` / `6` |
| `AI_RATE_INPUT_TIYIN_PER_MTOK` / `AI_RATE_OUTPUT_TIYIN_PER_MTOK` | прайс-лист сметы: тыйын за миллион токенов запроса / ответа; `0` — только счётчики | `0` / `0` |
| `WA_RATE_UTILITY_TIYIN` / `WA_RATE_MARKETING_TIYIN` / `WA_RATE_AUTHENTICATION_TIYIN` | тыйын за одно платное сообщение WhatsApp по категории Меты | `0` |
| `TRIAL_WARNING_DAYS` | за сколько дней предупредить о конце триала | `3` |
| `JOB_RUN_RETENTION_DAYS` | сколько хранить журнал запусков джоб | `30` |
| `STORAGE_ROOT` | каталог файлов на диске (в докере — volume `./storage`) | `storage` | Решение: S3 не используется — папка `storage` на сервере, общий volume `./storage` у api и worker (docker-compose).
| `UPLOAD_MAX_BYTES` | потолок размера загрузки | `20971520` (20 МБ) |
| `UPLOAD_CT_MAX_BYTES` | свой потолок для КТ (DICOM или ZIP исследования); ⚠️ вместе с ним — `client_max_body_size` в nginx | `1073741824` (1 ГиБ) |
| `TRIAL_DAYS` / `CARD_ATTACH_DEADLINE_DAYS` | правила триала | `30` / `7` |
