"""Таблица аккаунтов (единый контур). Телефон = логин.

Способ входа определяется флагом ``is_platform_admin``:

* **Команда Skrezo** — телефон + пароль (``password_hash``, argon2). Единственный
  контур с паролем; аккаунты заводит скрипт ``app/scripts/create_platform_user.py``.
* **Все остальные** — владельцы клиник, менеджеры, работники, соло-врачи — входят
  по коду из SMS: ``password_hash IS NULL``, подтверждение при каждом входе.

Инвариант после миграции ``0038``: ``password_hash IS NOT NULL`` ⇔ аккаунт
команды Skrezo. Он держится с обеих сторон — OTP-вход не логинит админа, а вход
по паролю не логинит всех прочих, даже если хеш у них каким-то образом появится.

**Блокировка** (``blocked_at``) — единственный способ отобрать доступ у человека
целиком. Исключение из клиники закрывает только её (``membership.active``), а
заблокированный не войдёт вовсе. Ставит и снимает её только команда Skrezo.
Строки из таблицы не удаляются: на аккаунте висят приёмы, платежи и журнал.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import DEFAULT_LANG, Lang, LangType


class Account(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "account"

    phone: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[Lang] = mapped_column(LangType, nullable=False, server_default=DEFAULT_LANG.value)
    onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Хеш пароля (argon2). NULL — вход по коду из SMS (все, кроме команды Skrezo).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # true — пароль временный, при следующем входе клиент обязан его сменить.
    # Осмыслен только вместе с паролем, то есть у аккаунтов команды Skrezo.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # true — сотрудник команды Skrezo (кросс-тенантный доступ к /admin/*, минует
    # клиничные проверки). Он же — единственный, кто входит по паролю.
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Момент блокировки; NULL — аккаунт действующий. Дата, а не флаг: «когда
    # заблокировали» спрашивают в поддержке, и хранить это отдельно негде.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Причина блокировки — показывается в админке рядом с датой.
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
