"""Dedicated deployment entry point for the private Agent Tool Facade."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from nha_trang_laundry_observability import (
    CORRELATION_HEADER,
    CorrelationContext,
    SafeStructuredLogger,
    correlation_scope,
)

from nha_trang_laundry_agent_tools.facade import router

app = FastAPI(
    title="Nha Trang Laundry Agent Tool Facade",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_LOGGER = SafeStructuredLogger()
app.include_router(router)


@app.middleware("http")
async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    context = CorrelationContext.from_http_header(request.headers.get(CORRELATION_HEADER))
    with correlation_scope(context):
        response = await call_next(request)
    response.headers[CORRELATION_HEADER] = context.header_value
    _LOGGER.record(
        component="agent-tools",
        name="http.request.completed",
        outcome="completed",
        correlation=context,
        fields={
            "method": request.method,
            "route": request.url.path,
            "status_code": response.status_code,
        },
    )
    return response


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
