"""Запись частичных правок (PATCH) в ORM-модель по явному списку колонок.

Связка «``model_dump(exclude_unset=True)`` → слепой ``setattr``» удобна, но
пишет в модель всё, что прислал клиент, и порождает сразу два дефекта:

* ``null`` в NOT NULL-колонку. В лучшем случае это 500 на ``flush()``; если же
  схема БД разойдётся с ORM, ``NULL`` доедет до коммита и необратимо испортит
  строку (так ломался ``appointment.scheduled_at``).
* Запись в поля, которых клиент касаться не должен. Любое будущее поле схемы,
  случайно совпавшее с именем колонки, начинает писаться молча — а ``clinic_id``
  в теле запроса уводит строку в чужую клинику (IDOR).

Поэтому список разрешённых колонок задаётся явно, рядом с репозиторием, а не
выводится из тела запроса.

Первая линия обороны — граница API: ``NotNullFieldsModel`` отвергает явный
``null`` ещё на валидации и отдаёт 422. Проверки здесь — вторая линия: они
ловят ошибку программиста (забытый ``NOT_NULL_FIELDS``, новый вызов в обход
схемы) и намеренно падают громко, а не портят данные.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any


class FieldUpdateError(ValueError):
    """Правка не прошла проверку по списку колонок.

    Наследует ``ValueError``: это ошибка программиста, а не клиента — до неё
    доходит только то, что уже проехало валидацию схемы.
    """


def apply_updates(
    entity: object,
    fields: Mapping[str, Any],
    *,
    updatable: Set[str],
    not_null: Set[str] = frozenset(),
    entity_name: str,
) -> None:
    """Записать в ``entity`` только разрешённые поля из ``fields``.

    :param entity: ORM-объект, который правим.
    :param fields: правки (обычно ``model_dump(exclude_unset=True)``).
    :param updatable: имена колонок, которые PATCH вправе менять.
    :param not_null: из них — объявленные в БД как NOT NULL; ``None`` для них
        запрещён.
    :param entity_name: название сущности в родительном падеже для сообщения
        об ошибке («приёма», «услуги»).
    :raises FieldUpdateError: если поля нет в ``updatable`` или в NOT NULL-поле
        пришёл ``None``.
    """
    unknown = fields.keys() - updatable
    if unknown:
        raise FieldUpdateError(f"Недопустимые поля {entity_name}: {sorted(unknown)}")
    for key, value in fields.items():
        if value is None and key in not_null:
            raise FieldUpdateError(f"Поле {key} не может быть NULL")
        setattr(entity, key, value)
