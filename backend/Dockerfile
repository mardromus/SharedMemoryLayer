# Build context: repository root (see docker-compose.yml)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# Bake the embedding model into the image so the container needs no network
# access (and no API key) to embed text at runtime.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ENV EMBEDDING_CACHE_DIR=/opt/models
RUN python -c "from fastembed import TextEmbedding; m = TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/opt/models'); print(len(list(m.embed(['warm up']))[0]))"

# Agent SDK (used by the LLM agent examples, the evaluation and SDK-driven tests)
COPY sdk /opt/sdk
RUN pip install /opt/sdk

COPY backend/ /app/
COPY evaluation /opt/evaluation
COPY scripts /opt/scripts

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser /app /opt/models /opt/evaluation \
    && chmod +x /app/entrypoint.sh
USER appuser

ENV HF_HUB_OFFLINE=1
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
