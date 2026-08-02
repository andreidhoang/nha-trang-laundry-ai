ARG PYTHON_BASE="python:3.12.13-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d"
FROM ${PYTHON_BASE} AS builder

ENV UV_VERSION="0.11.32" \
    UV_PROJECT_ENVIRONMENT="/opt/venv" \
    UV_COMPILE_BYTECODE="1" \
    UV_LINK_MODE="copy"
WORKDIR /build
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock README.md ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/public-agent-tools/pyproject.toml apps/public-agent-tools/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY packages/db/pyproject.toml packages/db/pyproject.toml
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/evals/pyproject.toml packages/evals/pyproject.toml
COPY packages/observability/pyproject.toml packages/observability/pyproject.toml
COPY packages/policy/pyproject.toml packages/policy/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package nha-trang-laundry-worker

FROM ${PYTHON_BASE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/apps/worker/src:/app/packages/contracts/src:/app/packages/db/src:/app/packages/domain/src:/app/packages/observability/src:/app/packages/policy/src" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1" \
    FEATURE_PUBLIC_CHANNELS_ENABLED="false" \
    FEATURE_AUTOMATED_SENDS_ENABLED="false" \
    FEATURE_AGENT_RUNTIME_ENABLED="false"
RUN apk add --no-cache --virtual .app-python-rundeps \
      gdbm=1.26-r0 libbz2=1.0.8-r6 libffi=3.5.2-r0 libncursesw=6.5_p20251123-r0 \
      libnsl=2.0.1-r1 libpanelw=6.5_p20251123-r0 libtirpc=1.3.5-r1 \
      libuuid=2.41.4-r0 readline=8.3.1-r0 xz-libs=5.8.3-r0 \
    && apk del --no-network .python-rundeps sqlite-libs \
    && rm -f /usr/local/lib/python3.12/lib-dynload/_sqlite3*.so \
    && addgroup -g 10001 -S app \
    && adduser -u 10001 -S -D -H -s /sbin/nologin -G app app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 apps/worker/src apps/worker/src
COPY --chown=10001:10001 packages/contracts/src packages/contracts/src
COPY --chown=10001:10001 packages/db/src packages/db/src
COPY --chown=10001:10001 packages/domain/src packages/domain/src
COPY --chown=10001:10001 packages/observability/src packages/observability/src
COPY --chown=10001:10001 packages/policy/src packages/policy/src
COPY --chown=10001:10001 specs/contracts specs/contracts
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"]
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["nha_trang_laundry_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
