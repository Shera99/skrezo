"""Служебные шаблоны Meta — каналу для проверки, пациентам не отправляются.

Meta заводит в каждом WABA тестовый ``hello_world`` («Welcome and
congratulations!! This message demonstrates…») для проверки канала. Он
одобрен и относится к utility, поэтому без фильтра попадал в каталог клиники
и предлагался кабинетом при закрытом окне 24 часов — так тестовый текст
ушёл реальному пациенту. Список имён — ``WA_SERVICE_TEMPLATE_NAMES``.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_service_template(meta_name: str, service_names: Iterable[str]) -> bool:
    """Имя шаблона входит в список служебных (регистр и пробелы не важны)."""
    name = meta_name.strip().lower()
    return any(name == service.strip().lower() for service in service_names)
