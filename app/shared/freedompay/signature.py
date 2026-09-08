"""Подпись сообщений FreedomPay (``pg_sig``).

Алгоритм из документации: имя скрипта, затем значения всех полей сообщения
в алфавитном порядке имён (кроме самой подписи), затем секретный ключ —
через «;» — и MD5 от получившейся строки. Подписываются и запросы, и
ответы в обе стороны; для ответа мерчанта на result_url имя скрипта —
последний сегмент URL.

Пример из документации: ``init_payment.php;25;Order description;<merchant>;
23;some_random_string;<secret>``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping


def sign(script: str, params: Mapping[str, object], secret: str) -> str:
    """MD5-подпись: скрипт ; значения по алфавиту имён ; секрет."""
    values = [str(params[key]) for key in sorted(params) if key != "pg_sig"]
    raw = ";".join([script, *values, secret])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify(script: str, params: Mapping[str, object], secret: str) -> bool:
    """Сверить ``pg_sig`` сообщения — без утечки времени сравнения."""
    expected = sign(script, params, secret)
    return hmac.compare_digest(expected, str(params.get("pg_sig") or ""))
