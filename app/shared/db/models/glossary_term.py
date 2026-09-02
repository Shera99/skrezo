"""Таблица глоссария — словарь фраз пациентов (§3.5 ТЗ ассистента).

Разговорные названия процедур, кыргызская лексика, транслит: «тиш ооруп
жатат» → «болит зуб». ``clinic_id IS NULL`` — общий словарь платформы,
клиника дополняет своим.

В промпт попадают ТОЛЬКО ``approved``-термины. ``pending`` — очередь
предложений: бот, не поняв фразу, переспрашивает пациента с вариантами и
после подтверждения кладёт пару сюда; администратор одним действием одобряет.
Автозаписи в действующий словарь нет намеренно: неверно понятая фраза стала
бы СИСТЕМНОЙ ошибкой (бот уверенно понимает неправильно всех следующих), а
глоссарий — каналом отравления промпта всей клиники.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import GlossaryStatus, GlossaryStatusType


class GlossaryTerm(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "glossary_term"
    __table_args__ = (
        # Промпт собирает approved-термины клиники (+ общие) на каждый ответ.
        Index("ix_glossary_term_clinic_status", "clinic_id", "status"),
    )

    # NULL — общий словарь платформы (поставляем мы).
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=True
    )
    phrase: Mapped[str] = mapped_column(Text, nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str | None] = mapped_column(Text, nullable=True)  # ru | ky | mixed
    # manual — админ/мы; assistant — предложение бота после подтверждения
    # пациентом (приходит pending); admin_feedback — из разбора эскалаций.
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    status: Mapped[GlossaryStatus] = mapped_column(
        GlossaryStatusType, nullable=False, default=GlossaryStatus.APPROVED
    )
