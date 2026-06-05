# syntax=docker/dockerfile:1.7

# ─── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir=/wheels \
       "bcrypt==4.2.1" \
       "psycopg2-binary==2.9.10" \
       "cryptography==44.0.0"

COPY . /build
RUN pip install --no-deps --no-index --find-links=/wheels .

# ─── Stage 2: runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 autopay \
    && useradd  --system --uid 1001 --gid autopay --create-home --shell /bin/bash autopay

WORKDIR /code

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=autopay:autopay . /code

USER autopay

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

COPY --chown=autopay:autopay scripts/entrypoint.sh /code/scripts/entrypoint.sh
RUN chmod +x /code/scripts/entrypoint.sh

ENTRYPOINT ["/code/scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
