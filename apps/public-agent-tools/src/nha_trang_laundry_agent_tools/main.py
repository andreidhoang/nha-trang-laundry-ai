"""Dedicated deployment entry point for the private Agent Tool Facade."""

from fastapi import FastAPI

from nha_trang_laundry_agent_tools.facade import router

app = FastAPI(
    title="Nha Trang Laundry Agent Tool Facade",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
