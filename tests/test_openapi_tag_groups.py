"""ReDoc показывает в меню только теги из ``x-tagGroups`` — проверяем полноту.

Так однажды «исчезли» разделы ассистента и WhatsApp: ручки в схеме были,
а в меню ReDoc — нет. Каждый тег обязан состоять в группе, каждая группа —
ссылаться только на существующие теги, и у каждого тега из роутов должно
быть описание.
"""

from __future__ import annotations

from app.main import app


def test_every_tag_is_listed_in_a_redoc_group() -> None:
    schema = app.openapi()
    declared = {t["name"] for t in schema["tags"]}
    used = {
        tag
        for path in schema["paths"].values()
        for op in path.values()
        for tag in op.get("tags", [])
    }
    grouped = [t for g in schema["x-tagGroups"] for t in g["tags"]]

    untagged = [
        f"{method.upper()} {path}"
        for path, item in schema["paths"].items()
        for method, op in item.items()
        if method in ("get", "post", "put", "patch", "delete") and not op.get("tags")
    ]
    assert not untagged, f"операции без тега не видны в ReDoc: {untagged}"
    assert used <= declared, f"теги роутов без описания: {sorted(used - declared)}"
    assert set(grouped) >= declared, f"теги вне групп ReDoc: {sorted(declared - set(grouped))}"
    assert set(grouped) <= declared, (
        f"группы ссылаются на неизвестные теги: {sorted(set(grouped) - declared)}"
    )
    assert len(grouped) == len(set(grouped)), "тег в двух группах"
