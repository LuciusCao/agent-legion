# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=22.17.0
ARG PYTHON_VERSION=3.13.5

FROM node:${NODE_VERSION}-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# velites-sandbox: the code-node sandbox wrapper as its own bin (issue #383).
# Built in its own stage so the worker image ships the sandbox without the
# agent harness (which #381 moved out of the image) and without a Rust
# toolchain. The name is deliberately not `velites` so worker runtime
# auto-detect (#254) cannot mistake it for the agent runtime executor.
FROM rust:1-bookworm AS velites-sandbox-build
WORKDIR /src
COPY velites/ ./velites/
RUN cargo build --release --locked --bin velites-sandbox --manifest-path velites/Cargo.toml

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

# Worker 镜像是纯执行服务（issue #381）：不含任何 agent runtime 执行器。
# velites agent runtime 以平台匹配的二进制外挂提供（compose bind mount 挂到
# /app/data/bin/velites，worker/binary_resolution.py 自带副本优先解析）。
# runtime 声明由启动时自动探测推导（issue #254），镜像内装什么 = 该
# worker 能跑什么。注意 pi 不适用外挂形态：其入口是 npm 包脚本，依赖
# node 运行时与包树（已随 #381 移出镜像）——pi 部署走裸机形态，docker
# 跑 pi 需自行构建含 node+pi 的镜像变体。
# code 池沙箱是镜像基础设施（issue #383）：velites-sandbox 与 agent runtime
# 无关，烤进镜像使 max_code_concurrency 不依赖外挂 velites。
FROM python:${PYTHON_VERSION}-slim-bookworm AS worker
# bubblewrap is velites-sandbox' Linux sandbox backend (EXEC-HARNESS-SANDBOX-001);
# the wrapper fails closed at startup without it unless --no-sandbox is set.
# Runtime requirements: bwrap needs either its setuid bit or unprivileged
# user namespaces, and a seccomp profile that allows unshare/clone — the
# default Docker seccomp profile blocks unshare, so deployments must relax
# it (validated on the real worker before M5; see velites-harness.md §5).
# velites 二进制外挂（#381）后 bwrap 仍是镜像必备：沙箱后端与二进制来源
# 无关，外挂的 velites agent runtime 同样依赖它。
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && chmod u+s /usr/bin/bwrap \
    && pip install --no-cache-dir "fastapi==0.116.1" "pyyaml==6.0.3" "uvicorn==0.35.0" "requests==2.34.2"
# Code-node sandbox wrapper (#383): /usr/local/bin is on PATH, so the
# resolve order (velites-sandbox → velites) finds it without any mount.
COPY --from=velites-sandbox-build /src/velites/target/release/velites-sandbox /usr/local/bin/velites-sandbox
WORKDIR /app
COPY worker /app/worker
# Neutral stdlib-only helpers shared by Host and worker (pi event
# compression / model-error detection); worker code must not import
# ``server`` — guarded by tests/workers/test_worker_import_isolation.py.
COPY shared /app/shared
COPY worker/client.py /usr/local/bin/agent_worker_client.py
COPY worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py
COPY --chmod=755 worker/cli.py /usr/local/bin/workerctl
# Smoke: the image must be able to import every worker entry point;
# a missing COPY fails the build here instead of crash-looping at runtime.
RUN python3 -c "import worker.service, worker.executor, worker.upload.queue"
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["python3", "-m", "worker.service"]
CMD ["--config", "/etc/agent-legion/worker.yaml", "--state-dir", "/var/lib/agent-legion-worker-control", "--host", "0.0.0.0", "--port", "8787"]
