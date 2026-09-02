"""Прогон регрессионного набора ассистента (§13.3).

Запускать **перед каждым изменением** промпта, базы знаний, глоссария или
модели:

    uv run python scripts/run_ai_regression.py

Выход отличен от нуля, если хоть один кейс провалился, — годится для CI и
для pre-commit. Живой уровень (ответ настоящей модели) сюда не входит: он
требует контекста клиники и гоняется через `/admin/ai/simulate-inbound` на
стенде.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.ai.domain.regression import run_all


def main() -> int:
    results = run_all()
    failed = [r for r in results if not r.ok]
    for result in results:
        if not result.ok:
            print(result)
    by_kind: dict[str, int] = {}
    for result in results:
        by_kind[result.case_id.split("-")[0]] = by_kind.get(result.case_id.split("-")[0], 0) + 1
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(by_kind.items()))
    print(f"\nкейсов: {len(results)} ({parts}); провалов: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
