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

.PHONY: dev-backend
dev-backend: ## 启动后端开发服务器 (127.0.0.1:8000)
	$(UV) run uvicorn server.app.main:app --reload --reload-dir server --timeout-graceful-shutdown 3 --host 127.0.0.1 --port 8000

.PHONY: dev-frontend
dev-frontend: ## 启动前端开发服务器
	cd $(FRONTEND_DIR) && $(NPM) run dev

AGENT_WORKER_CONFIG ?= config/agent-worker.yaml
AGENT_WORKER_STATE_DIR ?= data/agent-worker-service
AGENT_WORKER_UI_HOST ?= 127.0.0.1
AGENT_WORKER_UI_PORT ?= 8787
.PHONY: dev-worker
dev-worker: ## 启动本机 Worker Service 与控制台
	$(UV) run python -m worker.service \
		--config "$(AGENT_WORKER_CONFIG)" --state-dir "$(AGENT_WORKER_STATE_DIR)" \
		--host "$(AGENT_WORKER_UI_HOST)" --port "$(AGENT_WORKER_UI_PORT)"

.PHONY: llm-gateway
llm-gateway: ## 从 Pi models.json 读取凭据并启动远程 LLM 网关
	$(UV) run python scripts/remote/llm_gateway.py \
		--host "$(LLM_GATEWAY_HOST)" --port "$(LLM_GATEWAY_PORT)" \
		--provider "$(LLM_GATEWAY_PROVIDER)" --models-json "$(PI_MODELS_JSON)"

.PHONY: stack-host-up
stack-host-up: ## 公司电脑：启动 PostgreSQL + Agent Legion Host + 本机 Worker
	docker compose -f deploy/compose.host.yaml up -d --build

.PHONY: stack-host-down
stack-host-down: ## 停止公司电脑 Agent Legion stack
	docker compose -f deploy/compose.host.yaml down

.PHONY: stack-worker-up
stack-worker-up: ## Worker 机器：仅启动 Agent Legion Worker
	docker compose -f deploy/compose.worker.yaml up -d --build

.PHONY: stack-worker-down
stack-worker-down: ## 停止 Worker 机器上的 Agent Legion Worker
	docker compose -f deploy/compose.worker.yaml down

STACK ?= host
.PHONY: stack-down
stack-down: ## 停止本机所有 Agent Legion stack（host 与 worker）
	-docker compose -f deploy/compose.host.yaml down
	-docker compose -f deploy/compose.worker.yaml down

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
