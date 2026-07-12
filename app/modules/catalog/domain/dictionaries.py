"""Данные справочников с переводами (единый источник для сид-миграции).

Порядок в списке = ``sort_order``. Языки ``ru``/``en`` заполнены; ``kg``/``kz``
по умолчанию берут ``ru`` как заглушку — требуют вычитки носителями
(терминология специализаций особенно).
"""

from __future__ import annotations

from app.shared.db.enums import Lang


def _t(ru: str, en: str, kg: str | None = None, kz: str | None = None) -> dict[Lang, str]:
    """Собрать перевод; kg/kz по умолчанию = ru (заглушка)."""
    return {Lang.RU: ru, Lang.EN: en, Lang.KG: kg or ru, Lang.KZ: kz or ru}


# Крупные города Кыргызстана (топонимы в kg/kz совпадают с ru-написанием).
CITIES: list[dict[Lang, str]] = [
    _t("Бишкек", "Bishkek"),
    _t("Ош", "Osh"),
    _t("Джалал-Абад", "Jalal-Abad"),
    _t("Каракол", "Karakol"),
    _t("Токмок", "Tokmok"),
    _t("Кара-Балта", "Kara-Balta"),
    _t("Узген", "Uzgen"),
    _t("Нарын", "Naryn"),
    _t("Талас", "Talas"),
    _t("Баткен", "Batken"),
    _t("Кызыл-Кия", "Kyzyl-Kiya"),
    _t("Кант", "Kant"),
    _t("Чолпон-Ата", "Cholpon-Ata"),
]

# Стоматологические специализации (kg/kz — заглушка = ru, на вычитку).
SPECIALIZATIONS: list[dict[Lang, str]] = [
    _t("Стоматолог-терапевт", "Therapist dentist"),
    _t("Стоматолог-хирург", "Oral surgeon"),
    _t("Стоматолог-ортопед", "Prosthodontist"),
    _t("Ортодонт", "Orthodontist"),
    _t("Пародонтолог", "Periodontist"),
    _t("Эндодонт", "Endodontist"),
    _t("Детский стоматолог", "Pediatric dentist"),
    _t("Имплантолог", "Implantologist"),
    _t("Гигиенист", "Dental hygienist"),
]
