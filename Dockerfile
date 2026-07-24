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
RUN pip install --no-cache-dir "fastapi==0.116.1" "pyyaml==6.0.3" "uvicorn==0.35.0" \
    && npm install --global "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version
WORKDIR /app
COPY worker /app/worker
COPY server/__init__.py /app/server/__init__.py
COPY server/app/__init__.py /app/server/app/__init__.py
COPY server/app/workflows/__init__.py /app/server/app/workflows/__init__.py
COPY server/app/workflows/pi_protocol.py /app/server/app/workflows/pi_protocol.py
COPY server/app/services/__init__.py /app/server/app/services/__init__.py
COPY server/app/services/pi_event_compression.py /app/server/app/services/pi_event_compression.py
COPY config/agent-worker.example.yaml /app/config/agent-worker.example.yaml
COPY worker/client.py /usr/local/bin/agent_worker_client.py
COPY worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py
COPY --chmod=755 worker/cli.py /usr/local/bin/workerctl
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["python3", "-m", "worker.service"]
CMD ["--config", "/etc/agent-legion/worker.yaml", "--state-dir", "/var/lib/agent-legion-worker-control", "--host", "0.0.0.0", "--port", "8787"]
