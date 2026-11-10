FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY sdk/python ./sdk/python
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime
RUN groupadd --gid 10001 leashbyte && useradd --uid 10001 --gid leashbyte --no-create-home leashbyte \
    && mkdir -p /data/profiles /data/vault /data/run-artifacts \
    && chown -R leashbyte:leashbyte /data
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder --chown=leashbyte:leashbyte /app/.venv /app/.venv
COPY --from=builder --chown=leashbyte:leashbyte /app/src /app/src
COPY LICENSE NOTICE /app/
COPY docs/python-licenses.json docs/THIRD_PARTY.md /app/docs/
COPY sdk/typescript/THIRD_PARTY_LICENSES.json /app/sdk/typescript/
USER leashbyte
EXPOSE 8080
CMD ["uvicorn", "leashbyte.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.13-slim AS browser
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY --from=builder /app/.venv /app/.venv
RUN playwright install --with-deps chrome \
    && groupadd --gid 10001 leashbyte \
    && useradd --uid 10001 --gid leashbyte --create-home leashbyte \
    && mkdir -p /data/profiles \
    && chown -R leashbyte:leashbyte /data /opt/playwright
COPY --from=builder --chown=leashbyte:leashbyte /app/src /app/src
COPY LICENSE NOTICE /app/
COPY docs/python-licenses.json docs/THIRD_PARTY.md /app/docs/
COPY sdk/typescript/THIRD_PARTY_LICENSES.json /app/sdk/typescript/
USER leashbyte
EXPOSE 8082
CMD ["uvicorn", "leashbyte.browser_service:create_browser_app", "--factory", "--host", "0.0.0.0", "--port", "8082"]
