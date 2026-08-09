"""Сборка роутов фоновых задач (админ-контур Skrezo)."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.jobs.presentation.routes import list_job_runs, worker_status

router = APIRouter(tags=["jobs"])
router.include_router(worker_status.router)
router.include_router(list_job_runs.router)
