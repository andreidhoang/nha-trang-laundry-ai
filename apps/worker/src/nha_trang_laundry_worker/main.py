"""Fail-closed worker process host; job execution remains disabled until configured."""

from fastapi import FastAPI

app = FastAPI(
    title="Nha Trang Laundry Internal Worker",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Process liveness only; it does not claim queue readiness or automation authority."""
    return {"status": "ok", "automation": "disabled"}
