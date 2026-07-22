# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=22.17.0
ARG PYTHON_VERSION=3.13.5

FROM node:${NODE_VERSION}-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:${PYTHON_VERSION}-slim-bookworm AS host
ARG UV_VERSION=0.11.21
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . ./
COPY --from=frontend /src/frontend/dist /app/frontend/dist
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]

FROM python:${PYTHON_VERSION}-slim-bookworm AS worker
ARG NODE_VERSION=22.17.0
ARG PI_VERSION=0.80.10
COPY --from=frontend /usr/local/ /usr/local/
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "pyyaml==6.0.3" \
    && npm install --global "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version
WORKDIR /app
COPY scripts/agent_worker.py /app/scripts/agent_worker.py
COPY config/agent-worker.example.yaml /app/config/agent-worker.example.yaml
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/scripts/agent_worker.py"]
CMD ["--config", "/etc/agent-legion/worker.yaml"]
