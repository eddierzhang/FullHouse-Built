# Single-container FullHouse: the built frontend and the API in one process.
#
# This is the image for the public demo and for free hosts that run one
# container (Render, Fly.io, Koyeb). It defaults to demo mode: a demo
# restaurant that is replaced at every start and daily at 04:00 UTC. No model
# key is needed: with none, the rule-based assistant runs; LLM_API_KEY or
# ANTHROPIC_API_KEY in the environment picks a model. Override the environment
# to run it for real (render.customer.yaml does).
#
#   docker build -t fullhouse . && docker run -p 8080:8000 fullhouse
#
# The multi-container setup in docker-compose.yml (nginx + API + Ollama) is
# the one to use with a local model.

FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Relative API URLs: the same process serves the page and the API.
ENV VITE_API_BASE=same-origin
RUN npm run build


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/app.db \
    STATIC_DIR=/app/static \
    DEMO_MODE=true \
    SCRIPTED_STEP_DELAY_SECONDS=0.8 \
    LOG_FORMAT=json \
    STORAGE_DIR=/data/files \
    PORT=8000

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=frontend /frontend/dist ./static
RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# One uvicorn process: runs stream through an in-process broker. Hosts such
# as Render assign the port through $PORT. In demo mode the app loads the
# demo restaurant itself on startup; otherwise seed_data fills an empty one.
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_data.py && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
