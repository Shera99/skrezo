"""Таблица аккаунтов (клиентский контур). Телефон = логин, вход по паролю.

Сотрудники клиник (владелец/менеджер/работник) входят в рабочее приложение по
паре ``phone`` + пароль (``password_hash``, argon2). OTP из этого контура убран —
он остаётся только для пациентского приложения (вне скоупа). Первичный пароль
владельцу/работнику выдаётся временным при провижининге/добавлении: флаг
``must_change_password`` заставляет сменить его при первом входе.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import DEFAULT_LANG, Lang, LangType


class Account(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "account"

    phone: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[Lang] = mapped_column(LangType, nullable=False, server_default=DEFAULT_LANG.value)
    onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Хеш пароля (argon2). NULL — пароль ещё не задан (аккаунт создан до перехода
    # на пароли); вход при NULL невозможен, требуется установка пароля.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # true — пароль временный, при следующем входе клиент обязан его сменить.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # true — сотрудник команды Skrezo (кросс-тенантный доступ к /admin/*, минует
    # клиничные проверки). Единый вход для всех: команда и сотрудники клиник.
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
