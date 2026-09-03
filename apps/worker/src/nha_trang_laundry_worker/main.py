"""Fail-closed worker host with separate liveness and dependency readiness."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from nha_trang_laundry_observability import configure_structured_logging

from .host import WorkerSettings, WorkerSupervisor


def create_app(
    settings: WorkerSettings | None = None,
    supervisor: WorkerSupervisor | None = None,
) -> FastAPI:
    # SHOP-OBSERVABILITY-001: the worker's structured events had the same silent sink as the
    # API's -- a logger below its level with no handler. Configured before the supervisor starts,
    # so a failure during startup is a line rather than a shrug.
    configure_structured_logging()
    effective_settings = settings or WorkerSettings()
    effective_supervisor = supervisor or WorkerSupervisor(effective_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        effective_supervisor.start()
        try:
            yield
        finally:
            effective_supervisor.stop()

    application = FastAPI(
        title="Nha Trang Laundry Internal Worker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.worker_supervisor = effective_supervisor

    @application.get("/healthz", include_in_schema=False)
    @application.get("/livez", include_in_schema=False)
    def livez() -> dict[str, object]:
        """Process liveness only; it is never automation or dependency authority."""

        return {
            "status": "alive",
            "automation": "disabled",
            "provider_send_available": False,
        }

    @application.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        snapshot = effective_supervisor.readiness()
        body = {
            "status": "ready" if snapshot.ready else "not_ready",
            "error_code": snapshot.last_error_code,
            "internal_outbox_enabled": effective_settings.worker_internal_outbox_enabled,
            "agent_queue_enabled": effective_settings.worker_agent_queue_enabled,
            "agent_provider_runtime_enabled": False,
            "public_channels_enabled": False,
            "automatic_sends_enabled": False,
            "provider_send_available": False,
        }
        return JSONResponse(
            body,
            status_code=status.HTTP_200_OK
            if snapshot.ready
            else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return application


app = create_app()
