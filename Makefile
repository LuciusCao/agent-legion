.PHONY: help
help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

UV            ?= uv
UV_CACHE_DIR  ?= .uv-cache
NPM           ?= npm
FRONTEND_DIR  ?= frontend
LLM_GATEWAY_PROVIDER ?= gateway
LLM_GATEWAY_HOST     ?= 127.0.0.1
LLM_GATEWAY_PORT     ?= 8788
# PI_MODELS_JSON 故意不设默认值：它指向本机 Pi CLI 的 models.json（通常
# ~/.pi/agent/models.json），属于机器特定路径，必须显式传入。
PI_MODELS_JSON       ?=

export UV_CACHE_DIR

# 依赖与开发
.PHONY: sync
sync: ## 同步 Python 依赖 (uv sync)
	$(UV) sync

# 本地执行器每个隔离子进程都要消耗文件描述符（管道/信号量/日志），macOS
# 默认软限制 256 会在高并发下触发 EMFILE；开发进程统一抬高。
DEV_NOFILE_LIMIT ?= 65535

# 开发端口默认避开生产实例占用的 8000/5173/8787（prod worktree 常驻）。
DEV_BACKEND_PORT  ?= 8001
DEV_FRONTEND_PORT ?= 5174

.PHONY: dev-backend
dev-backend: ## 启动后端开发服务器 (127.0.0.1:$(DEV_BACKEND_PORT))
	ulimit -n $(DEV_NOFILE_LIMIT); $(UV) run uvicorn server.app.main:app --reload --reload-dir server --timeout-graceful-shutdown 3 --host 127.0.0.1 --port $(DEV_BACKEND_PORT)

.PHONY: dev-frontend
dev-frontend: ## 启动前端开发服务器（代理 /api 到 $(DEV_BACKEND_PORT)）
	cd $(FRONTEND_DIR) && VITE_API_TARGET="http://127.0.0.1:$(DEV_BACKEND_PORT)" $(NPM) run dev -- --port $(DEV_FRONTEND_PORT)

AGENT_WORKER_CONFIG ?= config/agent-worker.yaml
AGENT_WORKER_STATE_DIR ?= data/agent-worker-service
AGENT_WORKER_UI_HOST ?= 127.0.0.1
AGENT_WORKER_UI_PORT ?= 8789
# macOS 下批跑期间防止系统睡眠（lid close / idle）；非 macOS 环境为空。
CAFFEINATE := $(shell command -v caffeinate 2>/dev/null)
.PHONY: dev-worker
dev-worker: ## 启动本机 Worker Service 与控制台（macOS 下经 caffeinate 防睡眠）
	ulimit -n $(DEV_NOFILE_LIMIT); $(if $(CAFFEINATE),$(CAFFEINATE) -is ,)$(UV) run python -m worker.service \
		--config "$(AGENT_WORKER_CONFIG)" --state-dir "$(AGENT_WORKER_STATE_DIR)" \
		--host "$(AGENT_WORKER_UI_HOST)" --port "$(AGENT_WORKER_UI_PORT)"

# dev-up/down/status：上面三个 dev-* target 的后台编排（日志 data/logs/dev-*.log）。
# 端口变量经环境透传，与直接 make dev-backend 等保持一致。
.PHONY: dev-up
dev-up: ## 一条起齐开发环境（backend + frontend + worker，后台运行，幂等）
	DEV_BACKEND_PORT="$(DEV_BACKEND_PORT)" DEV_FRONTEND_PORT="$(DEV_FRONTEND_PORT)" \
		AGENT_WORKER_UI_PORT="$(AGENT_WORKER_UI_PORT)" ./scripts/dev_stack.sh up

.PHONY: dev-down
dev-down: ## 停止 dev-up 启动的全部开发进程（幂等）
	DEV_BACKEND_PORT="$(DEV_BACKEND_PORT)" DEV_FRONTEND_PORT="$(DEV_FRONTEND_PORT)" \
		AGENT_WORKER_UI_PORT="$(AGENT_WORKER_UI_PORT)" ./scripts/dev_stack.sh down

.PHONY: dev-status
dev-status: ## 查看开发环境各组件运行状态与 URL
	DEV_BACKEND_PORT="$(DEV_BACKEND_PORT)" DEV_FRONTEND_PORT="$(DEV_FRONTEND_PORT)" \
		AGENT_WORKER_UI_PORT="$(AGENT_WORKER_UI_PORT)" ./scripts/dev_stack.sh status

.PHONY: llm-gateway
llm-gateway: ## 启动远程 LLM 网关（凭据来自 PI_MODELS_JSON 指定的 Pi models.json，必须显式传入）
	@if [ -z "$(PI_MODELS_JSON)" ]; then \
		echo "错误：请显式指定 PI_MODELS_JSON=<path>/models.json（Pi CLI 的 provider 配置，通常 ~/.pi/agent/models.json）" >&2; \
		exit 1; \
	fi
	$(UV) run python scripts/remote/llm_gateway.py \
		--host "$(LLM_GATEWAY_HOST)" --port "$(LLM_GATEWAY_PORT)" \
		--provider "$(LLM_GATEWAY_PROVIDER)" --models-json "$(PI_MODELS_JSON)"

# 本机 compose 覆盖（如 deploy/compose.local.yaml，bind-mount 既有数据目录等
# 机器特定配置）：存在即自动并入 stack 命令，不存在则只用基础编排。
COMPOSE_HOST_FILES  := -f deploy/compose.host.yaml $(if $(wildcard deploy/compose.local.yaml),-f deploy/compose.local.yaml,)
COMPOSE_WORKER_FILES := -f deploy/compose.worker.yaml $(if $(wildcard deploy/compose.worker.local.yaml),-f deploy/compose.worker.local.yaml,)

.PHONY: stack-host-up
stack-host-up: ## 部署机：启动 PostgreSQL + Agent Legion Host + 本机 Worker
	docker compose $(COMPOSE_HOST_FILES) up -d --build

# 生产环境启停（仅 prod worktree 使用）：默认本机原生形态（后端 8000 含 SPA +
# worker 8787）；Docker stack 形态收编为参数 `make prod-up docker` /
# `make prod-down docker`（PostgreSQL + Host + Worker，secrets 预检 + 健康等待）。
.PHONY: docker
docker:
	@:

.PHONY: prod-up
prod-up: ## 启动生产环境（默认原生形态；`make prod-up docker` 走 Docker stack）
ifeq ($(filter docker,$(MAKECMDGOALS)),docker)
	./scripts/stack-prod-up.sh
else
	./scripts/native-prod-up.sh
endif

.PHONY: prod-down
prod-down: ## 停止生产环境（默认原生形态，SIGTERM 优雅停机；`make prod-down docker` 停 Docker stack）
ifeq ($(filter docker,$(MAKECMDGOALS)),docker)
	docker compose $(COMPOSE_HOST_FILES) down
else
	./scripts/native-prod-down.sh
endif

.PHONY: stack-host-down
stack-host-down: ## 停止部署机 Agent Legion stack
	docker compose $(COMPOSE_HOST_FILES) down

.PHONY: stack-worker-up
stack-worker-up: ## Worker 机器：仅启动 Agent Legion Worker
	docker compose $(COMPOSE_WORKER_FILES) up -d --build

.PHONY: stack-worker-down
stack-worker-down: ## 停止 Worker 机器上的 Agent Legion Worker
	docker compose $(COMPOSE_WORKER_FILES) down

STACK ?= host
.PHONY: stack-down
stack-down: ## 停止本机所有 Agent Legion stack（host 与 worker）
	-docker compose $(COMPOSE_HOST_FILES) down
	-docker compose $(COMPOSE_WORKER_FILES) down

.PHONY: stack-logs
stack-logs: ## 跟踪 stack 日志（STACK=host 或 worker，默认 host）
	docker compose -f deploy/compose.$(STACK).yaml logs -f

.PHONY: stack-status
stack-status: ## 查看 stack 容器与健康状态（STACK=host 或 worker，默认 host）
	docker compose -f deploy/compose.$(STACK).yaml ps

.PHONY: worker-status
worker-status: ## 通过本地 Worker Service 查看连接与运行状态
	$(UV) run python -m worker.cli status

.PHONY: worker-logs
worker-logs: ## 通过本地 Worker Service 查看最近日志
	$(UV) run python -m worker.cli logs

# 质量门
.PHONY: check-smoke
check-smoke: ## 运行冒烟质量门（静态 + smoke 测试层）
	GATE_TIER=smoke ./scripts/check-quick.sh

.PHONY: check-quick
check-quick: ## 运行快速质量门
	./scripts/check-quick.sh

.PHONY: check
check: ## 运行完整质量门 (提交前使用)
	./scripts/check.sh

.PHONY: check-ci
check-ci: ## 运行 CI 质量门
	./scripts/check-ci.sh

.PHONY: audit
audit: ## 依赖漏洞审计 (pip-audit + npm audit)
	./scripts/check-deps-audit.sh

# Skill 维护
.PHONY: import-demo
import-demo: ## 幂等导入 demo skills，并确保 skill lock、节点代码及示例 workspace 已 seed
	./scripts/import-demo.sh
	PYTHONPATH=. $(UV) run python -m scripts.seed_demo

.PHONY: skills-lock
skills-lock: ## 刷新 DB skill lock（global_settings skill_lock 文档）
	PYTHONPATH=. $(UV) run python -m server.app.skills.lock

# 架构预算与契约
.PHONY: architecture-ratchet
architecture-ratchet: ## 更新架构预算基线
	$(UV) run python -m scripts.ratchet_architecture_budgets

.PHONY: architecture-check
architecture-check: ## 检查架构契约
	$(UV) run python -m scripts.check_architecture

# 前端 API 类型生成
.PHONY: api-generate
api-generate: ## 重新生成前端 API 类型
	cd $(FRONTEND_DIR) && $(NPM) run api:generate

# 预提交钩子
.PHONY: install-hooks
install-hooks: ## 安装 worktree 兼容的本地质量门钩子
	./scripts/install-git-hooks.sh
