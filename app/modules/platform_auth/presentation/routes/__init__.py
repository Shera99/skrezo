"""Модуль платформенного доступа.

Собственных роутов больше не публикует: вход объединён в единый `/auth/*`
(модуль `identity`). Здесь остаётся только гвард `get_authenticated_user_id`
(в `deps.py`) для эндпоинтов `/admin/*`.
"""

from __future__ import annotations
