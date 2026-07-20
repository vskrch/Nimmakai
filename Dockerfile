# syntax=docker/dockerfile:1
# Production image for DigitalOcean App Platform / Droplets.

FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx vite build --outDir /build/static-dist --emptyOutDir

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    SQLITE_PATH=/data/nimmakai.db \
    CATALOG_SNAPSHOT_PATH=/data/catalog_snapshot.json \
    PROVIDERS_OVERLAY_PATH=/data/providers.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip \
    && pip install . \
    && SITE=$(python -c "import nimmakai, pathlib; print(pathlib.Path(nimmakai.__file__).parent)") \
    && mkdir -p "$SITE/static/dist" \
    && chown -R appuser:appuser /app "$SITE"

# Dashboard assets into the installed package (survives site-packages layout)
COPY --from=frontend /build/static-dist /tmp/static-dist
RUN SITE=$(python -c "import nimmakai, pathlib; print(pathlib.Path(nimmakai.__file__).parent)") \
    && cp -a /tmp/static-dist/. "$SITE/static/dist/" \
    && chown -R appuser:appuser "$SITE/static" \
    && rm -rf /tmp/static-dist

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/health" || exit 1

CMD ["sh", "-c", "exec uvicorn nimmakai.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
