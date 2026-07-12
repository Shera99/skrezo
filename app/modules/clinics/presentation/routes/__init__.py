"""Модуль провижининга.

HTTP-роут создания аккаунта переехал в раздел «Клиенты» (`POST /admin/clients`).
Здесь остаётся сервис/use-case провижининга (`ProvisioningService`,
`CreateClinicUseCase`), переиспользуемый разделом клиентов. Собственных роутов
модуль больше не публикует.
"""

from __future__ import annotations
