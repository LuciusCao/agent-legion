.PHONY: help
help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

UV            ?= uv
UV_CACHE_DIR  ?= .uv-cache
NPM           ?= npm
FRONTEND_DIR  ?= frontend

export UV_CACHE_DIR

# 依赖与开发
.PHONY: sync
sync: ## 同步 Python 依赖 (uv sync)
	$(UV) sync

.PHONY: dev-backend
dev-backend: ## 启动后端开发服务器 (port 8000)
	$(UV) run uvicorn server.app.main:app --reload --reload-dir server --port 8000

.PHONY: dev-frontend
dev-frontend: ## 启动前端开发服务器
	cd $(FRONTEND_DIR) && $(NPM) run dev

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

# Skill 维护
.PHONY: skills-lock
skills-lock: ## 刷新 config/skills.lock
	PYTHONPATH=. $(UV) run python server/app/skills/lock.py

# 架构预算与契约
.PHONY: architecture-ratchet
architecture-ratchet: ## 更新架构预算基线
	$(UV) run python scripts/ratchet_architecture_budgets.py

.PHONY: architecture-check
architecture-check: ## 检查架构契约
	$(UV) run python scripts/check_architecture.py

# 前端 API 类型生成
.PHONY: api-generate
api-generate: ## 重新生成前端 API 类型
	cd $(FRONTEND_DIR) && $(NPM) run api:generate

# 预提交钩子
.PHONY: install-hooks
install-hooks: ## 安装可选的预提交钩子
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
