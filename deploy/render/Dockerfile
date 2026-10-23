# Single-container image for Render (and any host that gives one web port):
# nginx (dashboard + reverse proxy) -> FastAPI backend, with an embedded Qdrant.
# PostgreSQL stays external (Render managed Postgres) and is the system of record;
# the vector index is rebuilt from it on start-up, so Qdrant needs no persistent disk.
# Build context: repository root.

FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM qdrant/qdrant:v1.13.4 AS qdrant

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx libunwind8 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ENV EMBEDDING_CACHE_DIR=/opt/models
RUN python -c "from fastembed import TextEmbedding; m = TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/opt/models'); print(len(list(m.embed(['warm up']))[0]))"

COPY --from=qdrant /qdrant/qdrant /usr/local/bin/qdrant
COPY backend/ /app/
COPY --from=frontend /app/dist /usr/share/nginx/html
COPY deploy/render/nginx.conf /etc/nginx/sml.conf.template
COPY infrastructure/nginx/real-ip.conf /etc/nginx/real-ip.conf
COPY deploy/render/reindex.py /app/reindex.py
COPY deploy/render/sync_admin.py /app/sync_admin.py
COPY deploy/render/start.sh /start.sh
RUN chmod +x /start.sh && mkdir -p /qdrant/storage

ENV HF_HUB_OFFLINE=1 \
    QDRANT_URL=http://127.0.0.1:6333 \
    QDRANT__SERVICE__HOST=127.0.0.1 \
    QDRANT__STORAGE__STORAGE_PATH=/qdrant/storage \
    QDRANT__TELEMETRY_DISABLED=true \
    PORT=10000
EXPOSE 10000
CMD ["/start.sh"]
