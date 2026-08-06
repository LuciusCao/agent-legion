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
PI_MODELS_JSON       ?= $(HOME)/.pi/agent/models.json

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

.PHONY: llm-gateway
llm-gateway: ## 从 Pi models.json 读取凭据并启动远程 LLM 网关
	$(UV) run python scripts/remote/llm_gateway.py \
		--host "$(LLM_GATEWAY_HOST)" --port "$(LLM_GATEWAY_PORT)" \
		--provider "$(LLM_GATEWAY_PROVIDER)" --models-json "$(PI_MODELS_JSON)"

.PHONY: seed-from-prod
seed-from-prod: ## 从本机 prod 的 Docker 生产库抽样灌数据到开发库
	$(UV) run python scripts/seed_from_prod.py

# 本机 compose 覆盖（如 deploy/compose.local.yaml，bind-mount 既有数据目录等
# 机器特定配置）：存在即自动并入 stack 命令，不存在则只用基础编排。
COMPOSE_HOST_FILES  := -f deploy/compose.host.yaml $(if $(wildcard deploy/compose.local.yaml),-f deploy/compose.local.yaml,)
COMPOSE_WORKER_FILES := -f deploy/compose.worker.yaml $(if $(wildcard deploy/compose.worker.local.yaml),-f deploy/compose.worker.local.yaml,)

.PHONY: stack-host-up
stack-host-up: ## 部署机：启动 PostgreSQL + Agent Legion Host + 本机 Worker
	docker compose $(COMPOSE_HOST_FILES) up -d --build

.PHONY: stack-prod-up
stack-prod-up: ## 一键启动本地生产 stack（secrets 检查 + 模型预热 + 健康等待）
	./scripts/stack-prod-up.sh

.PHONY: native-prod-up
native-prod-up: ## 一键启动原生（非 Docker）生产环境（后端含 SPA + Worker）
	./scripts/native-prod-up.sh

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
.PHONY: skills-lock
skills-lock: ## 刷新 config/skills.lock
	PYTHONPATH=. $(UV) run python server/app/skills/lock.py

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

# 审题信息上传工具
.PHONY: upload
upload: ## 上传审题信息包 (WORKSPACE/CONFIG/BATCH/PACKAGE)
	$(UV) run python tools/comprehension-uploader/run.py upload \
		$(if $(WORKSPACE),--workspace $(WORKSPACE)) \
		$(if $(CONFIG),--config $(CONFIG)) \
		$(if $(BATCH),--batch-id $(BATCH)) \
		$(ARGS) \
		$(PACKAGE)

.PHONY: scan-comprehension
scan-comprehension: ## 扫描审题信息 fingerprint 变化 (CONFIG/OUTPUT)
	$(UV) run python tools/comprehension-uploader/run.py scan \
		$(if $(CONFIG),--config $(CONFIG)) \
		$(if $(OUTPUT),--output $(OUTPUT))

.PHONY: package-comprehension
package-comprehension: ## 从 comprehension_info.json 生成 package.jsonl (INPUT_DIR/CONFIG/OUTPUT)
	$(UV) run python tools/comprehension-uploader/run.py package \
		$(if $(INPUT_DIR),--input-dir $(INPUT_DIR)) \
		$(if $(CONFIG),--config $(CONFIG)) \
		$(if $(OUTPUT),--output $(OUTPUT))

.PHONY: upload-workspace-package
upload-workspace-package: ## 从 workspace zip 直接上传审题信息 (CONFIG/PACKAGE/WORKSPACE/BATCH)
	$(UV) run python tools/comprehension-uploader/run.py upload \
		$(if $(CONFIG),--config $(CONFIG)) \
		$(if $(WORKSPACE),--workspace $(WORKSPACE)) \
		$(if $(BATCH),--batch-id $(BATCH)) \
		$(if $(PACKAGE),--workspace-package $(PACKAGE))
