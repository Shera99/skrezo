"""Генератор Postman-коллекции (v2.1) из OpenAPI платформенной зоны Skrezo.

Поднимает приложение, читает ``app.openapi()`` и собирает готовую к импорту
коллекцию:

* папки-РАЗДЕЛЫ по группам ``x-tagGroups`` (Вход / Админ-панель / Рабочее
  приложение / Публичное и служебное) с под-папками по тегам — ровно как разделы
  сайдбара ReDoc;
* ЕДИНАЯ схема авторизации — Bearer с авто-логином:
  - вход один для всех (``POST /auth/login`` по телефону + паролю). Скрипт
    захватывает ``accessToken`` / ``refreshToken`` в переменные коллекции и
    подставляет во все запросы;
  - ``POST /auth/select-clinic`` (для мультиклиничных аккаунтов) и
    ``POST /auth/refresh`` обновляют токен в тех же переменных;
  - публичные ручки (вход, обновление токена, справочники городов/специализаций,
    лид-форма, ``/health``) — без авторизации;
* примеры тел запросов из JSON-схем OpenAPI (тела auth — через переменные).

Ответы сервиса НЕ обёрнуты в конверт ``data`` — модели возвращаются как есть,
а ключи сериализуются в camelCase (``alias_generator=to_camel``).

Запуск:  ``python scripts/gen_postman_collection.py``
Результат: ``docs/skrezo-platform.postman_collection.json``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Корень проекта в путь импорта, чтобы скрипт запускался как ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app

# ─── Конфигурация ───────────────────────────────────────────────────────────

OUTPUT = (
    Path(__file__).resolve().parent.parent / "docs" / "skrezo-platform.postman_collection.json"
)

# Префикс модульных роутов (см. ``settings.api_v1_prefix``).
API = "/api/v1"

# Пути авторизации (единый вход по телефону + паролю).
_AUTH_LOGIN = f"{API}/auth/login"
_AUTH_SELECT = f"{API}/auth/select-clinic"
_AUTH_REFRESH = f"{API}/auth/refresh"
_AUTH_LOGOUT = f"{API}/auth/logout"

# Публичные ручки (без Bearer): вход, обновление токена, справочники, лиды, health.
PUBLIC_PATHS: set[str] = {
    _AUTH_LOGIN,
    _AUTH_REFRESH,
    f"{API}/cities",
    f"{API}/specializations",
    f"{API}/leads",
    "/health",
}

# Значения по умолчанию для path-переменных (становятся переменными коллекции).
# Идентификаторы — UUID v4, поэтому подставляем нулевой UUID-placeholder; номера
# зубов (FDI) — двузначные.
_UUID_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
PATH_VAR_DEFAULTS: dict[str, str] = {
    "fdi": "11",
    "tooth": "11",
    "id": "1",
}

# Тела auth-запросов через переменные коллекции (login → select → refresh
# заполняются автоматически). Ключи — camelCase (сериализация Pydantic).
AUTH_BODY_OVERRIDES: dict[str, str] = {
    _AUTH_LOGIN: '{\n  "phone": "{{phone}}",\n  "password": "{{password}}"\n}',
    _AUTH_SELECT: '{\n  "clinicId": "{{clinic_id}}"\n}',
    _AUTH_REFRESH: '{\n  "refreshToken": "{{refresh_token}}"\n}',
    _AUTH_LOGOUT: '{\n  "refreshToken": "{{refresh_token}}"\n}',
}

# ─── Генерация примеров из JSON-схемы ───────────────────────────────────────


def _resolve(schema: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    """Развернуть верхнеуровневый ``$ref``."""
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return comps.get(name, {})
    return schema


def example_for(schema: dict[str, Any], comps: dict[str, Any], depth: int = 0) -> Any:
    """Сгенерировать показательное значение по JSON-схеме (examples → default → тип)."""
    if depth > 6:
        return None
    schema = _resolve(schema, comps)

    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]

    # Объединения (``str | None`` → anyOf[string, null]): берём первый не-null вариант.
    for key in ("anyOf", "oneOf"):
        if key in schema:
            variants = [v for v in schema[key] if v.get("type") != "null"]
            if variants:
                return example_for(variants[0], comps, depth + 1)
            return None
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            merged.update(_resolve(part, comps))
        return example_for(merged, comps, depth + 1)

    t = schema.get("type")
    if t == "object" or "properties" in schema:
        result: dict[str, Any] = {}
        for name, prop in schema.get("properties", {}).items():
            result[name] = example_for(prop, comps, depth + 1)
        return result
    if t == "array":
        item = example_for(schema.get("items", {}), comps, depth + 1)
        return [item] if item is not None else []
    if t in ("integer", "number"):
        # Строковый формат UUID/дата приходит с type=string; сюда попадают числа.
        return 0
    if t == "boolean":
        return True
    if t == "string":
        fmt = schema.get("format")
        if fmt == "uuid":
            return _UUID_PLACEHOLDER
        if fmt in ("date-time", "date"):
            return "2026-01-01T00:00:00Z" if fmt == "date-time" else "2026-01-01"
        return "string"
    return None


# ─── Сборка элементов Postman ───────────────────────────────────────────────


def to_path_segments(path: str) -> list[str]:
    """``/api/v1/clients/{id}`` → ``['api','v1','clients','{{id}}']`` (path-vars как переменные)."""
    segments = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            segments.append("{{" + seg[1:-1] + "}}")
        else:
            segments.append(seg)
    return segments


def build_url(path: str, op: dict[str, Any]) -> dict[str, Any]:
    segments = to_path_segments(path)
    query = []
    for p in op.get("parameters", []):
        if p.get("in") != "query":
            continue
        val = example_for(p.get("schema", {}), {}, 0)
        query.append(
            {
                "key": p["name"],
                "value": "" if val is None else str(val),
                "disabled": True,
                "description": p.get("description", ""),
            }
        )
    raw = "{{base_url}}/" + "/".join(segments)
    if query:
        raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in query)
    url: dict[str, Any] = {"raw": raw, "host": ["{{base_url}}"], "path": segments}
    if query:
        url["query"] = query
    return url


def build_body(method: str, path: str, op: dict[str, Any], comps: dict[str, Any]):
    """Вернуть (body, headers-добавки) для запроса."""
    rb = op.get("requestBody")
    if not rb:
        return None, []
    content = rb.get("content", {})

    if "application/json" in content:
        if path in AUTH_BODY_OVERRIDES:
            raw = AUTH_BODY_OVERRIDES[path]
        else:
            example = example_for(content["application/json"]["schema"], comps, 0)
            raw = json.dumps(example, ensure_ascii=False, indent=2)
        body = {
            "mode": "raw",
            "raw": raw,
            "options": {"raw": {"language": "json"}},
        }
        return body, [{"key": "Content-Type", "value": "application/json"}]

    return None, []


# Скрипты, захватывающие токены в переменные коллекции (ответ — без конверта,
# ключи camelCase).
LOGIN_SCRIPT = [
    "const d = pm.response.json();",
    "if (d && d.accessToken) { pm.collectionVariables.set('access_token', d.accessToken); }",
    "if (d && d.refreshToken) { pm.collectionVariables.set('refresh_token', d.refreshToken); }",
    "if (d && d.activeClinicId) { pm.collectionVariables.set('clinic_id', d.activeClinicId); }",
    "pm.test('login ok', () => pm.response.code === 200);",
    "if (d && d.mustChangePassword) {",
    "  console.warn('Пароль временный — вызовите POST /auth/change-password.');",
    "}",
]
SELECT_SCRIPT = [
    "const d = pm.response.json();",
    "if (d && d.accessToken) { pm.collectionVariables.set('access_token', d.accessToken); }",
    "if (d && d.refreshToken) { pm.collectionVariables.set('refresh_token', d.refreshToken); }",
    "pm.test('select ok', () => pm.response.code === 200);",
]
REFRESH_SCRIPT = [
    "const d = pm.response.json();",
    "if (d && d.accessToken) { pm.collectionVariables.set('access_token', d.accessToken); }",
    "pm.test('refresh ok', () => pm.response.code === 200);",
]


def request_auth(path: str) -> dict[str, Any] | None:
    """Авторизация запроса-исключения; ``None`` → Bearer коллекции по умолчанию."""
    if path in PUBLIC_PATHS:
        return {"type": "noauth"}
    return None


def special_event(path: str) -> list[dict[str, Any]] | None:
    scripts = {
        _AUTH_LOGIN: LOGIN_SCRIPT,
        _AUTH_SELECT: SELECT_SCRIPT,
        _AUTH_REFRESH: REFRESH_SCRIPT,
    }
    if path in scripts:
        return [{"listen": "test", "script": {"type": "text/javascript", "exec": scripts[path]}}]
    return None


def build_item(method: str, path: str, op: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    body, headers = build_body(method, path, op, comps)
    summary = op.get("summary") or f"{method.upper()} {path}"
    request: dict[str, Any] = {
        "method": method.upper(),
        "header": headers,
        "url": build_url(path, op),
    }
    desc = op.get("description")
    if desc:
        request["description"] = desc
    if body is not None:
        request["body"] = body
    auth = request_auth(path)
    if auth is not None:
        request["auth"] = auth
    item: dict[str, Any] = {"name": summary, "request": request, "response": []}
    event = special_event(path)
    if event is not None:
        item["event"] = event
    return item


def main() -> None:
    app = create_app()
    schema = app.openapi()
    comps = schema.get("components", {}).get("schemas", {})

    # Группируем операции по первому тегу каждой операции.
    folders: dict[str, list[dict[str, Any]]] = {}
    for path in schema["paths"]:
        for method, op in schema["paths"][path].items():
            if method not in ("get", "post", "patch", "put", "delete"):
                continue
            tag = (op.get("tags") or ["Прочее"])[0]
            folders.setdefault(tag, []).append(build_item(method, path, op, comps))

    # Раскладываем теги по разделам ровно как в ReDoc (x-tagGroups) → под-папки.
    items: list[dict[str, Any]] = []
    grouped_tags: set[str] = set()
    for group in schema.get("x-tagGroups", []):
        subfolders = [
            {"name": tag, "item": folders[tag]} for tag in group["tags"] if tag in folders
        ]
        grouped_tags.update(group["tags"])
        if subfolders:
            items.append({"name": group["name"], "item": subfolders})
    # Теги вне групп (страховка) — плоскими папками в конец.
    for tag, tag_items in folders.items():
        if tag not in grouped_tags:
            items.append({"name": tag, "item": tag_items})

    path_vars = sorted(
        {
            seg[2:-2]
            for path in schema["paths"]
            for seg in to_path_segments(path)
            if seg.startswith("{{")
        }
    )
    variables = [
        # Порт как в docker-compose (8010): 8000 на dev-машинах обычно занят соседним сервисом.
        {"key": "base_url", "value": "http://localhost:8010", "type": "string"},
        # Единый вход: телефон/пароль для авто-логина; токены заполняются скриптами.
        {"key": "phone", "value": "+996700000000", "type": "string"},
        {"key": "password", "value": "s3cret-pa55word", "type": "string"},
        {"key": "access_token", "value": "", "type": "string"},
        {"key": "refresh_token", "value": "", "type": "string"},
    ]
    for var in path_vars:
        default = PATH_VAR_DEFAULTS.get(var, _UUID_PLACEHOLDER)
        variables.append({"key": var, "value": default, "type": "string"})

    collection = {
        "info": {
            "name": "Skrezo Platform API",
            "_postman_id": "skrezo-platform-0001",
            "description": (
                "API **платформенной зоны Skrezo** (админ-панель команды) и "
                "**рабочего приложения клиник**. Папки повторяют разделы ReDoc "
                "(`x-tagGroups`).\n\n"
                "**Авторизация — единый Bearer с авто-логином:**\n"
                "• задайте `base_url`, `phone`, `password`;\n"
                "• выполните «Вход → Вход по телефону и паролю (login)» — "
                "`access_token` сохранится и подставится во все запросы автоматически;\n"
                "• для мультиклиничных аккаунтов вызовите «Выбрать активную клинику "
                "(select-clinic)» — токен обновится в контексте клиники;\n"
                "• если `mustChangePassword = true`, смените временный пароль через "
                "`POST /auth/change-password`.\n\n"
                "Публичные ручки (вход, обновление токена, справочники городов и "
                "специализаций, лид-форма, `/health`) идут без авторизации.\n\n"
                "Ответы НЕ обёрнуты в конверт `data`; ключи — camelCase. Идентификаторы — "
                "UUID v4, суммы — целые сомы.\n\n"
                "Для запросов с `{...}` в пути задайте переменные коллекции "
                "(`clinic_id`, `patient_id`, `service_id` и т.д.).\n\n"
                "Сгенерировано из OpenAPI (`scripts/gen_postman_collection.py`)."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        # Схема по умолчанию — Bearer c access-токеном; публичные ручки
        # переопределяют auth на уровне запроса (noauth).
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}],
        },
        "item": items,
        "variable": variables,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {_count_requests(items)} запросов, {len(items)} разделов → {OUTPUT}")


def _count_requests(items: list[dict[str, Any]]) -> int:
    """Рекурсивно считает листовые запросы (для лога)."""
    n = 0
    for it in items:
        if "item" in it:
            n += _count_requests(it["item"])
        else:
            n += 1
    return n


if __name__ == "__main__":
    main()
