# Agent 配置治理 - 详细实现计划

## 总览

**目标**：将 Agent 配置从 YAML 迁移到 DB，实现 workspace 默认 + 节点覆盖的分层配置，退役 `workflows.pi.*`。

**总工作量**：7 天（按 Phase 1-4 推进）

**关键原则**：
- 严格用户配置，无 YAML bootstrap，无全局默认
- AgentDefinition 纯净（capability/runtime/skill/tools），执行配置外置
- versioned_entities 统一表，VersionedEntityService 统一生命周期管理
- pi.* 完全退役 → execution.*
- **扩展性预留**：支持未来本地 Agent 执行和 Worker 执行 Code 节点

---

## 扩展性设计（未来支持）

### 场景 1：本地 Agent 执行（Host 直接运行 Agent，不经过 Worker）

**当前架构**：Agent → AgentExecutionBroker → Worker claim → 远程执行

**未来需求**：Host 本地直接运行 Agent（调试、小规模部署、无 Worker 场景）

**扩展性预留**：

1. **LocalAgentExecutor**：实现与 Worker 相同的 Agent 执行协议，但运行在 Host 进程内
   ```python
   class LocalAgentExecutor:
       """在 Host 本地执行 Agent，复用 velites/pi runtime。"""
       def execute(self, manifest: dict) -> AgentResult: ...
   ```

2. **AgentExecutionBroker 扩展**：支持路由到 local executor
   ```python
   # 当前：只支持 Worker claim
   # 未来：支持 local executor 直接执行
   if agent.runtime == "local":
       return LocalAgentExecutor().execute(manifest)
   else:
       return self.enqueue_for_worker(manifest)
   ```

3. **配置层面**：AgentDefinition 增加 `runtime: "local"` 选项，或 workspace 配置 `agent_execution_mode: local|worker`

**对当前设计的影响**：
- `execution.*` manifest 结构保持不变，LocalAgentExecutor 直接消费
- AgentService 不需要改动，只是多一种 runtime 类型
- 需要在 Phase 1 的 `AgentDefinition.runtime` 字段预留 `"local"` 选项

### 场景 2：Worker 执行 Code 节点（Code 执行下沉到 Worker）

**当前架构**：Code 节点 → Host 本地 code executor（沙箱 Python 进程）

**未来需求**：Code 节点也分发到 Worker 执行（资源隔离、横向扩展、GPU 节点）

**扩展性预留**：

1. **Worker 协议扩展**：支持 code execution claim
   ```python
   # 当前 Worker 只 claim agent executions
   # 未来：支持 claim code executions
   POST /api/agent-executions/claim
   {
     "worker_id": "...",
     "supported_kinds": ["agent", "code"]  # 新增 code 类型
   }
   ```

2. **CodeExecutionBroker**：类似 AgentExecutionBroker，但管理 code 执行请求
   ```python
   class CodeExecutionBroker:
       """管理 code 节点的分布式执行。"""
       def enqueue(self, request: CodeExecutionRequest) -> str: ...
       def claim(self, worker_id: str) -> CodeExecution | None: ...
   ```

3. **统一定位**：Code 节点和 Agent 节点都走"分布式执行"模式
   ```
   ┌─────────────────────────────────────┐
   │  Execution Broker (统一入口)         │
   ├─────────────────────────────────────┤
   │  AgentExecutionBroker  │  CodeExecutionBroker │
   │  (agent 节点)          │  (code 节点)         │
   └─────────────────────────────────────┘
              ↓                      ↓
         Worker claim           Worker claim
   ```

**对当前设计的影响**：
- `versioned_entities` 表不需要改动（code 定义和 agent 定义都在里面）
- 需要在 Phase 1 的 DB schema 中预留 `code_executions` 表（或复用 `agent_execution_requests` 表加 `kind` 字段）
- Code executor 的 sandbox 逻辑需要打包成 Worker 可执行的形式

### 统一抽象：ExecutionRequest

无论是 Agent 还是 Code，未来的分布式执行可以统一为：

```python
@dataclass
class ExecutionRequest:
    """统一的执行请求，支持 Agent 和 Code。"""
    execution_id: str
    kind: Literal["agent", "code"]      # 执行类型
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    entity_id: str                      # agent_id 或 node_code_id
    entity_version: int
    manifest: dict                      # 执行配置（当前已统一为 execution.*）
    bundle_path: Path | None            # 执行包（agent 有，code 未来有）
```

**当前 Phase 1-4 的预留**：
- `versioned_entities.entity_type` 已区分 `node_code` 和 `agent`，未来可扩展 `code_execution`
- `execution.*` manifest 结构通用，Agent 和 Code 都可使用
- Workspace Settings 的 `default_agent_*` 配置未来可扩展为 `default_execution_*`

---

## Phase 1：数据模型扩展 + 统一表（2 天）

### 目标
- 创建 `versioned_entities` 统一表
- 迁移现有 `workflow_node_codes` 和 `agent_definitions` 数据
- 扩展 `workspaces` 表加 Agent 默认配置字段
- 实现 `VersionedEntityService` 抽象层

### 文件清单

#### 新建文件

| 文件 | 说明 |
|---|---|
| `server/app/db/migrations/versioned_entities.py` | DB migration：创建 versioned_entities 表 + 数据迁移 |
| `server/app/services/versioned_entity_service.py` | VersionedEntityService 抽象基类 |
| `server/app/services/agent_service.py` | AgentService 实现（继承 VersionedEntityService） |
| `server/app/models/versioned_entity.py` | VersionedEntity Pydantic 模型 |
| `tests/services/test_versioned_entity_service.py` | Service 层单元测试 |
| `tests/services/test_agent_service.py` | AgentService 单元测试 |
| `tests/db/test_versioned_entities_migration.py` | Migration 测试 |

#### 修改文件

| 文件 | 改动点 |
|---|---|
| `server/app/db/schema.py` | SCHEMA_VERSION 提升到 26，注册新 migration |
| `server/app/db/migrations/__init__.py` | 导出新 migration 函数 |
| `server/app/services/node_codes.py` | 重构为继承 VersionedEntityService，迁移数据访问到新表 |
| `server/app/agent_catalog.py` | AgentDefinition 保持纯净（不含 provider/model），移除 sync_agent_definitions |
| `server/app/db/postgres_schema.sql` | 更新 schema 定义 |

### DB Migration SQL

```sql
-- v26: versioned_entities 统一表 + workspaces Agent 默认配置

-- 1. 创建统一表
create table if not exists versioned_entities (
  id text primary key,
  entity_type text not null check(entity_type in ('node_code', 'agent')),
  workspace_id text,               -- Agent 自 schema v46 起为 workspace 作用域；NULL 仅用于全局 executor/出厂 node_code
  entity_key text not null,
  version integer not null,
  status text not null check(status in ('draft', 'published', 'archived')),
  definition_json text not null,
  definition_hash text not null,
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  published_at timestamptz,
  unique(entity_type, workspace_id, entity_key, version)
);

create unique index if not exists versioned_entities_published
  on versioned_entities(entity_type, workspace_id, entity_key)
  where status = 'published';

create index if not exists idx_versioned_entities_type_key
  on versioned_entities(entity_type, entity_key);

-- 2. 迁移 workflow_node_codes 数据
insert into versioned_entities (
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by, created_at, published_at
)
select
  id, 'node_code', workspace_id, node_key, version, status,
  json_build_object('code', code, 'code_hash', code_hash, 'change_note', change_note)::text,
  code_hash, created_by, created_at, published_at
from workflow_node_codes;

-- 3. 迁移 agent_definitions 数据（保持纯净定义）
insert into versioned_entities (
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by, created_at, published_at
)
select
  agent_id || ':v1', 'agent', null, agent_id, 1, 'published',
  definition_json, definition_hash, 'system', updated_at, updated_at
from agent_definitions
where enabled = 1;

-- 4. workspaces 表加 Agent 默认配置
alter table workspaces
  add column if not exists default_agent_provider text not null default '',
  add column if not exists default_agent_model text not null default '',
  add column if not exists default_agent_thinking text not null default 'low';

-- 5. 记录 schema 版本
insert into schema_migrations(version, name) values (26, 'versioned_entities');
```

### 关键代码结构

```python
# server/app/models/versioned_entity.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass(frozen=True)
class VersionedEntity:
    id: str
    entity_type: Literal["node_code", "agent"]
    workspace_id: str | None
    entity_key: str
    version: int
    status: Literal["draft", "published", "archived"]
    definition_json: str
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None

# server/app/services/versioned_entity_service.py
from abc import ABC, abstractmethod

class VersionedEntityService(ABC):
    def __init__(self, job_db: JobQueries): ...

    def list_entities(self, entity_type: str, workspace_id: str | None = None) -> list[VersionedEntity]: ...
    def get_published(self, entity_type: str, entity_key: str, workspace_id: str | None = None) -> VersionedEntity | None: ...
    def get_entity(self, entity_id: str) -> VersionedEntity | None: ...
    def create_draft(self, entity_type: str, entity_key: str, definition: dict, workspace_id: str | None, created_by: str) -> VersionedEntity: ...
    def update_draft(self, entity_id: str, definition: dict) -> VersionedEntity: ...
    def publish(self, entity_id: str) -> VersionedEntity: ...
    def archive(self, entity_id: str) -> VersionedEntity: ...
    def rollback(self, entity_id: str, version: int, created_by: str) -> VersionedEntity: ...
    def list_versions(self, entity_type: str, entity_key: str, workspace_id: str | None = None) -> list[VersionedEntity]: ...
    def copy_entity(self, source_id: str, new_key: str, created_by: str) -> VersionedEntity: ...

# server/app/services/agent_service.py
class AgentService(VersionedEntityService):
    ENTITY_TYPE = "agent"

    def list_agents(self) -> list[AgentDefinition]: ...
    def get_agent(self, agent_id: str) -> AgentDefinition | None: ...
    def create_agent(self, definition: AgentDefinition, created_by: str) -> VersionedEntity: ...
    def update_agent(self, agent_id: str, definition: AgentDefinition) -> VersionedEntity: ...
    def publish_agent(self, agent_id: str) -> VersionedEntity: ...
```

### 测试策略

- **单元测试**：VersionedEntityService 所有方法（CRUD、版本化、状态机）
- **Migration 测试**：验证数据从旧表迁移正确
- **集成测试**：AgentService 与 DB 交互
- **兼容性测试**：确保现有 Node Code 功能不受影响

---

## Phase 2：Workspace Settings + Studio Agents 页（3 天）

### 目标
- Workspace Settings 新增 Agent 默认配置区块
- Studio 新增 Agents 页签（Agent 定义管理）
- Skill 选择组件（folder picker / 路径输入 + tag 下拉）
- API 端点实现

### 文件清单

#### 新建文件（后端）

| 文件 | 说明 |
|---|---|
| `server/app/routes/agents.py` | Agent CRUD API 路由 |
| `server/app/routes/agent_contracts.py` | Agent API Pydantic 模型 |
| `server/app/routes/skills.py` | Skill 校验 API |
| `server/app/services/skill_validator.py` | Skill 路径校验和 tag 读取 |
| `tests/routes/test_agents.py` | Agent API 测试 |
| `tests/routes/test_skills.py` | Skill API 测试 |

#### 新建文件（前端）

| 文件 | 说明 |
|---|---|
| `frontend/src/pages/SettingsPage/AgentDefaultsSection.tsx` | Workspace Agent 默认配置区块 |
| `frontend/src/pages/workflowStudio/AgentsTab.tsx` | Studio Agents 页签主组件 |
| `frontend/src/pages/workflowStudio/AgentList.tsx` | Agent 列表 |
| `frontend/src/pages/workflowStudio/AgentEditor.tsx` | Agent 编辑器 |
| `frontend/src/pages/workflowStudio/AgentVersionHistory.tsx` | 版本历史 |
| `frontend/src/components/SkillSelector.tsx` | Skill 选择组件（folder picker / 路径输入） |
| `frontend/src/api/agents.ts` | Agent API 客户端 |
| `frontend/src/api/skills.ts` | Skill API 客户端 |
| `frontend/src/types/agent.ts` | Agent 类型定义 |

#### 修改文件（后端）

| 文件 | 改动点 |
|---|---|
| `server/app/routes/__init__.py` | 注册 agents 路由 |
| `server/app/routes/workspace_settings.py` | 新增 agent-defaults 端点 |
| `server/app/services/workspace_configuration.py` | 处理 agent 默认配置更新 |
| `server/app/services/workspace_settings_payload.py` | 返回 agent 默认配置 |

#### 修改文件（前端）

| 文件 | 改动点 |
|---|---|
| `frontend/src/pages/SettingsPage.tsx` | 集成 AgentDefaultsSection |
| `frontend/src/pages/WorkflowStudioPage.tsx` | 新增 Agents 页签 |
| `frontend/src/stores/settingStore.ts` | 加 agent 默认配置状态 |
| `frontend/src/types/index.ts` | 导出 Agent 类型 |

### API 详细设计

```
# Agent Definition CRUD
GET    /api/agents
  Response: [{ agent_id, capability, runtime, skill, version, status, published_at }]

POST   /api/agents
  Body: { agent_id, capability, runtime, skill, tools, config_schema }
  Response: { id, agent_id, version, status: "draft" }

GET    /api/agents/{agent_id}
  Response: { agent_id, capability, runtime, skill, tools, config_schema, version, status }

GET    /api/agents/{agent_id}/versions
  Response: [{ version, status, created_by, created_at, published_at }]

PATCH  /api/agents/{agent_id}/draft
  Body: { capability?, runtime?, skill?, tools?, config_schema? }
  Response: { id, version, status: "draft" }

POST   /api/agents/{agent_id}/publish
  Response: { id, version, status: "published" }

POST   /api/agents/{agent_id}/archive
  Response: { id, version, status: "archived" }

POST   /api/agents/{agent_id}/rollback
  Body: { version }
  Response: { id, version: new_version, status: "draft" }

POST   /api/agents/{agent_id}/copy
  Body: { new_agent_id }
  Response: { id, agent_id: new_agent_id, version, status: "draft" }

# Workspace Agent 默认配置
GET    /api/workspaces/{id}/settings/agent-defaults
  Response: { provider, model, thinking }

PATCH  /api/workspaces/{id}/settings/agent-defaults
  Body: { provider?, model?, thinking? }
  Response: { provider, model, thinking }

# Skill 校验
POST   /api/skills/validate
  Body: { path }
  Response: { valid: true, tags: ["v1.0", "v1.1"], latest_tag: "v1.1" }

GET    /api/skills/tags?path={path}
  Response: { tags: ["v1.0", "v1.1"], latest_tag: "v1.1" }

# Worker 兼容性
GET    /api/agents/{agent_id}/compatibility?workspace_id={id}
  Response: { compatible: true, matched_workers: [...], missing_models: [...] }
```

### UI 组件结构

```
SettingsPage
├── BasicInfoSection (现有)
├── AgentDefaultsSection (新增)
│   ├── ProviderInput
│   ├── ModelInput
│   ├── ThinkingSelect
│   └── SaveButton
├── ExecutorAllocationSection (现有)
└── ...

WorkflowStudioPage
├── WorkflowsTab (现有)
├── NodeCodesTab (现有)
└── AgentsTab (新增)
    ├── AgentList (左栏)
    │   ├── AgentListItem (每个 Agent)
    │   └── CopyButton
    ├── AgentEditor (右栏)
    │   ├── BasicInfoForm (agent_id, capability, runtime)
    │   ├── SkillSelector (folder picker / path input + tag dropdown)
    │   ├── ToolsSelect
    │   ├── ConfigSchemaEditor (JSON)
    │   └── ActionButtons (Save Draft / Publish / Archive)
    └── AgentVersionHistory (抽屉/弹窗)
        ├── VersionList
        └── RollbackButton
```

### 测试策略

- **API 测试**：所有端点的成功/失败场景
- **Service 测试**：AgentService 与 VersionedEntityService 集成
- **UI 组件测试**：AgentEditor, SkillSelector, AgentDefaultsSection
- **E2E 测试**：完整 Agent 创建 → 发布 → workflow 使用流程

---

## Phase 3：退役 YAML + 严格校验（1 天）

### 目标
- 停止 YAML sync
- 严格校验（无配置即报错）
- 删除 YAML 配置
- Manifest 清理（只保留 execution.*）

### 文件清单

#### 修改文件

| 文件 | 改动点 |
|---|---|
| `server/app/agent_catalog.py` | 移除 `sync_agent_definitions`，改为只读 DB |
| `server/app/agent_broker/dispatch.py` | Manifest 构建改用 `execution.*`，移除 `pi.*` fallback |
| `server/app/agent_broker/manifest_guard.py` | 校验逻辑更新（检查 execution.model） |
| `server/app/workflows/velites_command.py` | 命令构建读 `execution.*` |
| `server/app/workflows/pi_protocol.py` | 更新或废弃（如果 pi 协议保留则改名） |
| `config/workflow.yaml` | 删除 `agents:` 和 `workflows.pi.*` |
| `server/app/settings.py` | 移除 pi 相关配置加载 |
| `server/app/executors/runtime_config.py` | 更新或废弃 pi 配置 |

### 严格校验逻辑

```python
# server/app/agent_broker/dispatch.py
def build_manifest(...):
    # 解析 model
    model = node.execution.model or workspace.default_agent_model
    if not model:
        raise ValueError(
            f"Node {node.key} requires model configuration. "
            f"Set workspace default in Settings (provider/model) "
            f"or override in Studio node settings."
        )

    # 解析 provider
    provider = node.execution.provider or workspace.default_agent_provider
    if not provider:
        raise ValueError(f"Node {node.key} requires provider configuration.")

    # 解析 thinking
    thinking = node.execution.thinking or workspace.default_agent_thinking

    manifest = {
        "execution": {
            "binary": runtime_binary,
            "provider": provider,
            "model": model,
            "thinking": thinking,
            ...
        }
    }
```

### 测试策略

- **严格校验测试**：无配置时明确报错
- **Fallback 测试**：节点覆盖 > workspace 默认
- **YAML 删除测试**：启动不再读 YAML agents
- **Manifest 结构测试**：只包含 execution.*，无 pi.*

---

## Phase 4：清理与文档（1 天）

### 目标
- 代码清理（移除 pi.* legacy）
- 文档更新
- 测试覆盖完善

### 文件清单

#### 删除文件

| 文件 | 说明 |
|---|---|
| `server/app/workflows/pi_config.py` | 如果完全退役则删除，否则重命名为 execution_config.py |
| `server/app/workflows/pi_command_builder.py` | 合并到 velites_command 或重命名 |

#### 修改文件

| 文件 | 改动点 |
|---|---|
| `docs/architecture/backend.md` | 更新 Agent 配置说明 |
| `docs/architecture/velites-harness.md` | 更新 manifest 结构说明 |
| `docs/agent-worker-deployment.md` | 更新部署配置说明 |
| `README.md` | 更新快速开始指南 |
| `AGENTS.md` | 更新 Agent 配置治理规则 |

### 文档更新要点

1. **Agent 配置流程**：
   - Workspace Settings → Agent 默认配置
   - Studio → Agents 页签 → Agent 定义管理
   - Studio → Workflow 编辑器 → 节点覆盖

2. **Manifest 结构**：
   - `execution.*` 替代 `pi.*`
   - model 解析链说明

3. **故障排查**：
   - "requires model configuration" 错误处理
   - Worker 兼容性检查

### 测试策略

- **回归测试**：全流程测试确保无破坏
- **文档测试**：文档中的命令和配置示例可执行
- **性能测试**：VersionedEntityService 查询性能

---

## 全局测试策略

### 单元测试覆盖

| 模块 | 测试文件 | 覆盖点 |
|---|---|---|
| VersionedEntityService | `tests/services/test_versioned_entity_service.py` | CRUD、版本化、状态机、并发 |
| AgentService | `tests/services/test_agent_service.py` | Agent 特有逻辑、复制、校验 |
| SkillValidator | `tests/services/test_skill_validator.py` | 路径校验、tag 读取、错误处理 |
| Manifest Builder | `tests/agent_broker/test_dispatch.py` | model 解析链、严格校验、execution.* 结构 |

### 集成测试覆盖

| 场景 | 测试文件 | 说明 |
|---|---|---|
| Agent 创建 → 发布 → 使用 | `tests/integration/test_agent_lifecycle.py` | 完整生命周期 |
| Workspace 默认 + 节点覆盖 | `tests/integration/test_agent_config_resolution.py` | 配置解析优先级 |
| YAML 退役 | `tests/integration/test_yaml_retirement.py` | 启动不读 YAML，严格校验 |
| Worker 兼容性 | `tests/integration/test_worker_compatibility.py` | 运行时校验、错误提示 |

### E2E 测试覆盖

| 场景 | 测试文件 | 说明 |
|---|---|---|
| Settings 配置 Agent 默认 | `tests/e2e/test_settings_agent_defaults.py` | UI 配置保存生效 |
| Studio 创建 Agent | `tests/e2e/test_studio_agent_create.py` | 完整创建发布流程 |
| Studio 节点覆盖 | `tests/e2e/test_studio_node_override.py` | 节点覆盖 workspace 默认 |
| Job 运行使用新配置 | `tests/e2e/test_job_with_agent_config.py` | 端到端验证 |

---

## 实施 Checklist

### Phase 1（Day 1-2）

- [ ] 创建 DB migration：versioned_entities 表 + 数据迁移
- [ ] 实现 VersionedEntity 模型
- [ ] 实现 VersionedEntityService 抽象
- [ ] 实现 AgentService
- [ ] 重构 NodeCodeService 继承 VersionedEntityService
- [ ] workspaces 表加 default_agent_* 字段
- [ ] 单元测试：VersionedEntityService, AgentService
- [ ] Migration 测试：数据迁移正确性
- [ ] 验证：现有 Node Code 功能正常

### Phase 2（Day 3-5）

- [ ] 实现 Agent CRUD API
- [ ] 实现 Skill 校验 API
- [ ] 实现 Workspace Agent 默认配置 API
- [ ] Settings UI：AgentDefaultsSection
- [ ] Studio UI：AgentsTab（AgentList + AgentEditor + AgentVersionHistory）
- [ ] SkillSelector 组件（folder picker / path input + tag dropdown）
- [ ] API 测试：所有端点
- [ ] UI 组件测试
- [ ] E2E 测试：Agent 创建发布流程
- [ ] 用户配置：为所有 workspace 配置默认 provider/model

### Phase 3（Day 6）

- [x] 移除 sync_agent_definitions YAML sync
- [x] Manifest 构建改用 execution.*，移除 pi.* fallback
- [x] 严格校验：无配置即报错
- [x] 删除 config/workflow.yaml 的 agents: 和 workflows.pi.*
- [x] 更新 manifest_guard 校验逻辑
- [x] 集成测试：YAML 退役、严格校验
- [x] 验证：生产环境正常运行

### Phase 4（Day 7）

- [x] 代码清理：pi_config/pi_command_builder/pi_runner 链随保留的本地 pi executor 死路径一并保留（executors/pi.py 仍在引用），仅清除 flavor 时代残留（worker 预检收紧为 runtime 钉死二进制）；load_agent_definitions / sync_agent_definitions / get_agent_definition 已随 Phase 3 删除
- [x] 文档更新：backend.md, velites-harness.md, deployment.md, README.md
- [x] AGENTS.md 更新（由仓库 owner 另行处理，本次不改）
- [x] 回归测试：全流程
- [x] 性能测试：VersionedEntityService 查询（published 缓存 5s TTL，热路径零 DB）
- [x] 最终验证：所有测试通过，文档完整

---

## 风险缓解 Checklist

- [ ] **数据迁移风险**：Phase 1 migration 先在测试环境验证，备份生产 DB
- [ ] **配置缺失风险**：Phase 3 前确保所有 workspace 已配置默认 provider/model
- [ ] **Worker 兼容性风险**：Phase 2 提供兼容性检查工具，提前发现不匹配
- [ ] **回滚风险**：保留 versioned_entities 历史版本，支持一键回滚
- [ ] **性能风险**：versioned_entities 表加索引，查询性能测试

---

## 下一步

1. **确认实施计划**：本文件是否需要调整？
2. **开始 Phase 1**：我可以立即开始实现 DB migration 和 VersionedEntityService
3. **并行准备**：你可以开始思考各 workspace 的默认 provider/model 配置

确认后我立即开始 Phase 1 实现。
