"""Ф8: ретеншн истории ассистента.

Журнал инструментов старше срока уходит; переписка закрытых диалогов старше
срока — вместе с медиа на диске; свежее и открытые диалоги остаются; резюме
не трогаются; закрытый диалог без сообщений и с давней активностью удаляется.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import app.jobs.tasks.ai_cleanup as cleanup
from app.core.config import settings
from app.shared.db.enums import AiConversationStatus, AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_summary import AiSummary
from app.shared.db.models.ai_tool_call import AiToolCall
from tests.api.conftest import Ctx
from tests.conftest import Resources


async def _conversation(
    res: Resources, ctx: Ctx, phone: str, *, status: AiConversationStatus, age_days: int
) -> uuid.UUID:
    async with res.factory() as session:
        conv = AiConversation(
            clinic_id=uuid.UUID(ctx.clinic_id),
            wa_phone=phone,
            patient_id=None,
            status=status,
            last_msg_at=datetime.now(UTC) - timedelta(days=age_days),
        )
        session.add(conv)
        await session.commit()
        return conv.id


async def _message(
    res: Resources, ctx: Ctx, conv_id: uuid.UUID, *, age_days: int, media_path: str | None = None
) -> int:
    async with res.factory() as session:
        msg = AiMessage(
            conversation_id=conv_id,
            clinic_id=uuid.UUID(ctx.clinic_id),
            role=AiMessageRole.PATIENT,
            body="привет",
            media_path=media_path,
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
        session.add(msg)
        await session.commit()
        return int(msg.id)


async def _tool_call(res: Resources, ctx: Ctx, conv_id: uuid.UUID, *, age_days: int) -> None:
    async with res.factory() as session:
        session.add(
            AiToolCall(
                conversation_id=conv_id,
                clinic_id=uuid.UUID(ctx.clinic_id),
                tool="get_services",
                args={},
                result=[],
                created_at=datetime.now(UTC) - timedelta(days=age_days),
            )
        )
        await session.commit()


async def _run(monkeypatch: pytest.MonkeyPatch, res: Resources) -> str:
    monkeypatch.setattr(cleanup, "session_factory", res.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )
    return await cleanup.cleanup_ai_history({})


async def test_retention_removes_only_old_history_of_closed_dialogs(
    ctx: Ctx, _resources: Resources, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "ai_tool_call_retention_days", 90)
    monkeypatch.setattr(settings, "ai_message_retention_days", 365)
    media = tmp_path / "whatsapp" / "2025" / "01" / "01"
    media.mkdir(parents=True)
    old_file = media / "old.ogg"
    old_file.write_bytes(b"ogg")
    fresh_file = media / "fresh.ogg"
    fresh_file.write_bytes(b"ogg")

    closed = await _conversation(
        _resources, ctx, "+996700990001", status=AiConversationStatus.CLOSED, age_days=400
    )
    open_ = await _conversation(
        _resources, ctx, "+996700990002", status=AiConversationStatus.ACTIVE, age_days=400
    )
    emptied = await _conversation(
        _resources, ctx, "+996700990003", status=AiConversationStatus.CLOSED, age_days=500
    )
    await _message(_resources, ctx, closed, age_days=400, media_path="whatsapp/2025/01/01/old.ogg")
    kept_recent = await _message(
        _resources, ctx, closed, age_days=10, media_path="whatsapp/2025/01/01/fresh.ogg"
    )
    kept_open = await _message(_resources, ctx, open_, age_days=400)
    await _message(_resources, ctx, emptied, age_days=500)
    await _tool_call(_resources, ctx, closed, age_days=120)
    await _tool_call(_resources, ctx, closed, age_days=5)
    async with _resources.factory() as session:
        session.add(
            AiSummary(
                clinic_id=uuid.UUID(ctx.clinic_id),
                conversation_id=closed,
                patient_id=None,
                wa_phone="+996700990001",
                body="давно записывалась на чистку",
                lang="ru",
                model="fake",
            )
        )
        await session.commit()

    summary = await _run(monkeypatch, _resources)
    assert "вызовов инструментов: 1" in summary
    assert "сообщений: 2 (файлов: 1)" in summary
    assert "пустых диалогов: 1" in summary

    async with _resources.factory() as session:
        left = set((await session.execute(select(AiMessage.id))).scalars())
        assert left == {kept_recent, kept_open}
        assert len((await session.execute(select(AiToolCall.id))).scalars().all()) == 1
        convs = set((await session.execute(select(AiConversation.id))).scalars())
        assert (
            closed in convs and open_ in convs and emptied not in convs
        )  # closed — ещё с сообщением
        assert (
            len((await session.execute(select(AiSummary.id))).scalars().all()) == 1
        )  # резюме живёт
    assert not old_file.exists() and fresh_file.exists()

    # Повтор — нечего удалять.
    assert "сообщений: 0 (файлов: 0), пустых диалогов: 0" in await _run(monkeypatch, _resources)
