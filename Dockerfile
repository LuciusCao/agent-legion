# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=22.17.0
ARG PYTHON_VERSION=3.13.5

FROM node:${NODE_VERSION}-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# velites: self-contained Rust agent harness binary (pi replacement, M4
# rollout). Built in its own stage so the worker image ships only the static
# binary without a Rust toolchain. velites/target is excluded via .dockerignore.
FROM rust:1-bookworm AS velites-build
WORKDIR /src
COPY velites/ ./velites/
RUN cargo build --release --locked --manifest-path velites/Cargo.toml

FROM python:${PYTHON_VERSION}-slim-bookworm AS host
ARG UV_VERSION=0.11.21
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
# g++：兜底编译无预编译 wheel 的 sdist 依赖。
# ffmpeg：视频管线（转写前转 wav、章节切片、yt-dlp 合并）在 host 上直接调用。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . ./
COPY --from=frontend /src/frontend/dist /app/frontend/dist
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uvicorn", "server.app.main:create_prod_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]

FROM python:${PYTHON_VERSION}-slim-bookworm AS worker
ARG NODE_VERSION=22.17.0
ARG PI_VERSION=0.80.10
COPY --from=frontend /usr/local/ /usr/local/
# bubblewrap is velites' Linux sandbox backend (EXEC-HARNESS-SANDBOX-001);
# the harness fails closed at startup without it unless --no-sandbox is set.
# Runtime requirements: bwrap needs either its setuid bit or unprivileged
# user namespaces, and a seccomp profile that allows unshare/clone — the
# default Docker seccomp profile blocks unshare, so deployments must relax
# it (validated on the real worker before M5; see velites-harness.md §5).
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && chmod u+s /usr/bin/bwrap \
    && pip install --no-cache-dir "fastapi==0.116.1" "pyyaml==6.0.3" "uvicorn==0.35.0" "requests==2.34.2" \
    && npm install --global "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version
# velites harness binary. Transition flavor: pi above stays installed and
# remains the default executor; switch a deployment by setting
# `workflows.pi.flavor: velites` (see docs/architecture/velites-harness.md).
# Once the rollout completes, drop the npm install + pi --version lines above.
COPY --from=velites-build /src/velites/target/release/velites /usr/local/bin/velites
WORKDIR /app
COPY worker /app/worker
# Neutral stdlib-only helpers shared by Host and worker (pi event
# compression / model-error detection); worker code must not import
# ``server`` — guarded by tests/workers/test_worker_import_isolation.py.
COPY shared /app/shared
COPY config/agent-worker.example.yaml /app/config/agent-worker.example.yaml
COPY worker/client.py /usr/local/bin/agent_worker_client.py
COPY worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py
COPY --chmod=755 worker/cli.py /usr/local/bin/workerctl
# Smoke: the image must be able to import every worker entry point;
# a missing COPY fails the build here instead of crash-looping at runtime.
RUN python3 -c "import worker.service, worker.executor, worker.upload_queue"
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["python3", "-m", "worker.service"]
CMD ["--config", "/etc/agent-legion/worker.yaml", "--state-dir", "/var/lib/agent-legion-worker-control", "--host", "0.0.0.0", "--port", "8787"]
