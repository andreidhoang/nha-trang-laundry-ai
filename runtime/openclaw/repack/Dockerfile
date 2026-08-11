ARG NODE_BASE="node:24.15.0-alpine3.23@sha256:d1b3b4da11eefd5941e7f0b9cf17783fc99d9c6fc34884a665f40a06dbdfc94f"
FROM ${NODE_BASE} AS builder

WORKDIR /workspace/runtime/openclaw/public-cell/plugin
COPY runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz \
  /workspace/runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz
COPY runtime/openclaw/public-cell/plugin/package.json \
  runtime/openclaw/public-cell/plugin/package-lock.json ./
COPY runtime/openclaw/public-cell/plugin/tsconfig.json \
  runtime/openclaw/public-cell/plugin/openclaw.plugin.json ./
COPY runtime/openclaw/public-cell/plugin/src ./src
RUN npm ci --ignore-scripts && npm run build

FROM ${NODE_BASE} AS runtime

RUN apk add --no-cache --upgrade \
      libcrypto3=3.5.7-r0 \
      libssl3=3.5.7-r0 \
    && rm -rf /usr/local/lib/node_modules/npm \
      /usr/local/bin/npm \
      /usr/local/bin/npx

ENV OPENCLAW_STATE_DIR="/tmp/openclaw" \
    OPENCLAW_CONFIG_PATH="/opt/openclaw/openclaw.json5" \
    REAL_CUSTOMER_DATA_ALLOWED="false" \
    PUBLIC_INGRESS_ENABLED="false" \
    PROVIDER_CALLS_ENABLED="false" \
    AUTOMATIC_SEND_ENABLED="false" \
    DIRECT_SEND_ENABLED="false"
WORKDIR /opt/openclaw
COPY --from=builder --chown=node:node \
  /workspace/runtime/openclaw/public-cell/plugin /opt/openclaw/plugin
COPY --chown=node:node runtime/openclaw/public-cell/openclaw.json5 /opt/openclaw/openclaw.json5
USER node
ENTRYPOINT ["node", "/opt/openclaw/plugin/node_modules/openclaw/openclaw.mjs"]
CMD ["--version"]
