# Agent 配置治理方案（v3 - 最终确认版）

> **状态（2026-08-05）**：Phase 1–4 已全部完成。yaml `agents:` 段与
> `workflows.pi` 块已退役（schema v27，`agent_definitions` 表已删除，
> published `runtime: pi` 定义已翻转为 velites）；Agent 定义经 Studio
> 管理并发布进 `versioned_entities`；manifest 执行块统一为 `execution.*`。
> 本文档保留为设计定稿记录。

## 背景与目标

### 现状问题

1. **YAML 配置不可控**：`config/workflow.yaml` 的 `agents:` 段在启动时 sync 到 DB，多环境共用同一份 YAML
2. **全局 model 配置**：`workflows.pi.model` 是全局兜底，无法支持不同 Agent/Workspace 使用不同模型
3. **velites 命名混乱**：velites 已经是独立 runtime，但 model/provider 仍走 `pi.*` 命名空间
4. **配置散落**：Agent 定义（YAML）+ 节点执行配置（workflow revision）+ 全局默认（pi.*）三层叠加

### 治理目标

- **严格用户配置**：没有 YAML bootstrap，没有全局默认，必须显式配置才能工作
- **Workspace 级默认 + 节点级覆盖**：provider/model/thinking 在 Settings 配 workspace 默认，Studio 节点可覆盖
- **产品化配置**：所有配置经 DB 管理，UI 操作，可审计
- **退役 YAML**：`config/workflow.yaml` 不再包含 `agents:` 和 `workflows.pi.*`
- **一等公民抽象**：Agent Definition 和 Custom Node Code 共享同一套 draft/publish 生命周期

## 已确认决策

| 决策点 | 结论 |
|---|---|
| provider/model 配置位置 | **Workspace Settings 默认 + Studio 节点覆盖**，不进 AgentDefinition |
| draft/publish 版本化 | **需要**，与 code-node 同为一等公民，抽象 VersionedEntity 复用 |
| pi.* 命名 | **完全退役**，统一为 `execution.*` |
| Worker 兼容性校验 | **运行时校验**（不阻塞 publish） |
| DB 表结构 | **合并为 `versioned_entities` 统一表** |
| Agent 创建 | **支持复制现有 Agent** |
| skill 选择 | **folder picker 或绝对路径输入 + 校验 + tag 下拉** |
| 节点级覆盖 | **允许**，与现有 Studio 编辑器一致 |
| thinking 配置 | **Workspace 默认 + 节点级覆盖** |

## 核心设计

### 1. 配置分层模型

```
┌─────────────────────────────────────┐
│  Studio 节点级覆盖                  │  node.execution.provider/model/thinking
│  (workflow revision 定义)           │  优先级最高，用于特殊节点
├─────────────────────────────────────┤
│  Workflow 顶层 execution 默认       │  定义级可选块，loader 合并进节点、
│  (workflow revision 定义)           │  随 revision 版本化
├─────────────────────────────────────┤
│  Agent Definition                   │  capability/runtime/skill/tools/config_schema
│  (versioned_entities 表)            │  纯净定义，不含执行配置
└─────────────────────────────────────┘
```

**model 解析链（严格模式）**：
```python
model = node.execution.model  # 节点覆盖，loader 已合并 workflow 顶层 execution 默认
if not model:
    raise ValueError(
        f"node {node.key} requires a model: set the node execution model "
        "in Studio or the workflow-level execution block."
    )
```

> 历史说明：早期版本还有第三层「Workspace Settings 默认」
> （`default_agent_provider/model/thinking` 三列），已随 schema v63 退役——
> 执行配置只存在于 versioned 的 workflow 定义里。

### 2. AgentDefinition（纯净版）

```python
class AgentDefinition(BaseModel):
    """Agent 的能力定义，不含执行配置。"""

    agent_id: str                    # 如 "demo-review-questions-v1"
    capability: str                  # 如 "review_questions"
    runtime: Literal["pi", "openclaw", "velites"]
    skill: str                       # 如 "education-video-problems-generation/review-questions"
    tools: tuple[str, ...] = ("read", "write", "bash")
    config_schema: dict[str, Any] = {}  # 节点可调参数 schema
    enabled: bool = True
```

**注意**：AgentDefinition **不包含** provider/model/thinking。这些执行配置由 workflow 定义（顶层默认 + Studio 节点覆盖）管理。

### 3. Workflow 顶层 execution 默认

```yaml
# workflow 定义（draft YAML）的可选顶层块，随 revision 版本化
key: my_workflow
execution:
  provider: "deepseek"        # 如 "deepseek", "openai", "gateway"
  model: "deepseek-v4-flash"  # 如 "deepseek-v4-flash", "gpt-5.2"
  thinking: "low"             # low / medium / high，可空 = runtime 决定
nodes:
  write_script:
    capability: write_script
    execution:                # 节点覆盖优先于顶层默认，逐字段合并
      model: "gpt-5.2"
```

loader 在定义加载时把顶层块逐字段合并进每个 agent 路由节点（节点值优先；
start/code 节点不受影响），dispatch 拿到的节点 execution 即是有效值。
Studio 节点编辑器的 provider/model 输入框按节点 Agent 的 runtime 给出在线
Worker 上报的可用选项（`GET /api/workspaces/{id}/runtime-models`），也允许
自由输入。

> 历史说明：本节原为「Workspace Settings 扩展」（workspaces 表
> `default_agent_*` 三列 + Settings「Agent 默认配置」区块），已随 schema
> v63 退役删列。

### 4. VersionedEntity 抽象（合并表）

```sql
create table versioned_entities (
  id text primary key,
  entity_type text not null check(entity_type in ('node_code', 'agent')),
  workspace_id text,               -- 新实体均为 workspace 作用域；NULL 仅见于迁移/回放兼容的历史记录
  entity_key text not null,        -- node_key 或 agent_id
  version integer not null,
  status text not null check(status in ('draft', 'published', 'archived')),
  definition_json text not null,   -- Agent 定义或 Node Code 代码
  definition_hash text not null,
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  published_at timestamptz,
  unique(entity_type, workspace_id, entity_key, version)
);

create unique index versioned_entities_published
  on versioned_entities(entity_type, workspace_id, entity_key)
  where status = 'published';
```

**统一 Service 接口**：
```python
class VersionedEntityService(ABC):
    def list(self, entity_type: str, workspace_id: str | None) -> list[VersionedEntity]: ...
    def get_published(self, entity_type: str, entity_key: str, workspace_id: str | None) -> VersionedEntity | None: ...
    def create_draft(self, entity_type: str, entity_key: str, definition: dict, workspace_id: str | None, created_by: str) -> VersionedEntity: ...
    def update_draft(self, entity_id: str, definition: dict) -> VersionedEntity: ...
    def publish(self, entity_id: str) -> VersionedEntity: ...
    def archive(self, entity_id: str) -> VersionedEntity: ...
    def rollback(self, entity_id: str, version: int, created_by: str) -> VersionedEntity: ...
    def list_versions(self, entity_type: str, entity_key: str, workspace_id: str | None) -> list[VersionedEntity]: ...
```

**具体实现**：
- `NodeCodeService(VersionedEntityService)` - 现有，迁移到新表
- `AgentService(VersionedEntityService)` - 新增

### 5. Manifest 构建改造（pi.* → execution.*）

```python
manifest = {
    "execution_id": "...",
    "workspace_id": "...",
    "job_id": "...",
    "workflow_key": "...",
    "node_key": "...",
    "agent_id": "...",
    "agent_definition_hash": "...",
    "runtime": "velites",
    "capability": "review_questions",
    "inputs": [...],
    "expected_outputs": [...],
    "config": {...},
    "tools": ["read", "write", "bash"],
    "skill": "education-video-problems-generation/review-questions",
    "skill_version": "abc123",
    "log_path": "...",
    "execution": {
        "binary": "velites",
        "provider": "deepseek",           # node.execution.provider（已合并 workflow 顶层默认）
        "model": "deepseek-v4-flash",        # node.execution.model（已合并 workflow 顶层默认）
        "thinking": "low",            # node.execution.thinking（已合并 workflow 顶层默认）
        "timeout_seconds": 1800,
        "no_sandbox": false,
    },
}
```

### 6. Agent 管理 UI（Settings + Studio 协同）

**不需要独立的 Agent 管理页**，而是融入现有结构：

#### Studio → Workflow 编辑器 → 执行配置（顶层默认 + 节点覆盖）

执行配置只存在于 workflow 定义里：顶层 `execution:` 块为整个 workflow
配一处，节点 `execution.*` 按需覆盖；节点编辑器的 provider/model 输入框
按节点 Agent 的 runtime 给出在线 Worker 上报的可用选项（datalist，可自由
输入），thinking 空值 = runtime 决定。（早期的 Workspace Settings
「Agent 默认配置」区块已随 schema v63 退役。）

#### Studio → Agents 页签（Agent 定义管理）

管理 AgentDefinition（capability/runtime/skill/tools），**不含** provider/model：

```
Studio
├── Workflows (现有)
├── Node Codes (现有)
└── Agents (新增)
    ├── Agent 列表（左栏）
    │   ├── question-key-info-v1 [published] generate_key_info / velites
    │   ├── video-subtitle-review-v1 [published] review_subtitles / pi
    │   └── ...
    ├── Agent 编辑器（右栏）
    │   ├── 基本信息：agent_id, capability, runtime
    │   ├── 技能工具：skill (folder picker / 路径输入 + tag 下拉), tools
    │   ├── 参数 Schema：config_schema (JSON editor)
    │   └── 版本历史 + 复制创建
    └── 操作：保存草稿 / 发布 / 归档 / 复制
```

#### Studio → Workflow 编辑器 → Agent 节点（执行配置覆盖）

在 workflow 节点上覆盖 workspace 默认：

```
┌────────────────────────────────────┐
│ 节点：generate_key_info             │
├────────────────────────────────────┤
│ 运行时设置                          │
│ Provider:  [继承默认 (deepseek)    ]   │
│ Model:     [继承默认 (deepseek-v4-flash)] │
│ Thinking:  [继承默认 (low) ▼]      │
│                                    │
│ ☑ 覆盖默认配置                      │
│ Provider:  [openai            ]    │
│ Model:     [gpt-5.2           ]    │
│ Thinking:  [high ▼]                │
└────────────────────────────────────┘
```

### 7. Skill 选择交互

**两种方式**：

**方式 A：Folder Picker**
1. 点击"选择技能目录"
2. 打开系统 folder picker，选中 skill 目录（如 `~/.agents/skills/agent-legion/education-video-problems-generation/review-questions/`）
3. App 校验目录包含 `SKILL.md`
4. 校验通过后，显示该 skill 的 tags（从 SKILL.md frontmatter 读取）
5. 用户从 tag 下拉选择（最新 tag 置顶）

**方式 B：绝对路径输入**
1. 用户输入绝对路径（如 `/Users/xxx/.agents/skills/agent-legion/education-video-problems-generation/review-questions`）
2. App 实时校验路径存在且包含 `SKILL.md`
3. 校验通过后，同样显示 tag 下拉选择

### 8. API 设计

```
# Agent Definition CRUD（versioned entity 模式）
GET    /api/agents                           # 列表（published 版本）
POST   /api/agents                           # 创建 draft
GET    /api/agents/{agent_id}                # 详情（当前 published）
GET    /api/agents/{agent_id}/versions       # 版本历史
PATCH  /api/agents/{agent_id}/draft          # 更新 draft
POST   /api/agents/{agent_id}/publish        # 发布
POST   /api/agents/{agent_id}/archive        # 归档
POST   /api/agents/{agent_id}/rollback       # 回滚
POST   /api/agents/{agent_id}/copy           # 复制创建新 Agent

# Runtime 可用模型聚合（Studio 节点 execution 下拉的选项来源）
GET    /api/workspaces/{id}/runtime-models

# Skill 校验与 tag 获取
POST   /api/skills/validate                  # 校验 skill 路径
GET    /api/skills/tags?path={path}          # 获取 skill tags

# Worker 兼容性（运行时）
GET    /api/agents/{agent_id}/compatibility?workspace_id={id}
```

## 迁移策略（4 阶段）

### Phase 1：数据模型扩展 + 统一表（2 天）

1. **DB schema 变更**：
   ```sql
   -- 创建统一表
   create table versioned_entities (...);
   
   -- 迁移现有 workflow_node_codes 数据
   insert into versioned_entities (entity_type, workspace_id, entity_key, ...)
   select 'node_code', workspace_id, node_key, ... from workflow_node_codes;
   
   -- 迁移现有 agent_definitions 数据（扩展字段）
   insert into versioned_entities (entity_type, entity_key, definition_json, ...)
   select 'agent', agent_id, definition_json || '{"provider":"","model":""}', ...
   from agent_definitions;
   
   -- workspaces 表加默认配置字段
   alter table workspaces
     add column default_agent_provider text not null default '',
     add column default_agent_model text not null default '',
     add column default_agent_thinking text not null default 'low';
   ```

2. **VersionedEntityService 抽象**：实现统一 Service 层

3. **Manifest 构建兼容**：双写 `pi.*` 和 `execution.*`

### Phase 2：Workspace Settings + Studio Agents 页（3 天）

1. **Workspace Settings UI**：Agent 默认配置区块
2. **Studio Agents 页签**：Agent 定义管理（CRUD + 版本化 + 复制）
3. **Skill 选择组件**：folder picker / 路径输入 + tag 下拉
4. **用户配置**：为所有 workspace 配置默认 provider/model

### Phase 3：退役 YAML + 严格校验（1 天）

1. **停止 YAML sync**：`sync_agent_definitions` 只读 DB
2. **严格校验**：workspace 无默认 model 且节点无覆盖时，Agent 节点报错
3. **删除 YAML**：`config/workflow.yaml` 移除 `agents:` 和 `workflows.pi.*`
4. **Manifest 清理**：只保留 `execution.*`

### Phase 4：清理与文档（1 天）

1. **代码清理**：移除 `pi.*` legacy 代码
2. **文档更新**：架构文档、部署文档、用户手册
3. **测试覆盖**：全流程测试

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 现有 Agent 无 provider/model，迁移后无法运行 | 高 | Phase 1 从 YAML 读取填充；提供批量配置脚本 |
| 多 workspace 配置不一致 | 中 | Settings UI 强制校验；导出/导入工具 |
| Skill 路径校验失败 | 低 | 清晰错误提示；支持多种路径格式 |
| 节点覆盖与 workspace 默认混淆 | 低 | UI 明确展示"继承默认" vs "覆盖"状态 |

## 实现计划预览

确认后我将输出：
1. **文件清单**：所有需要新建/修改的文件
2. **改动点**：每个文件的具体改动
3. **测试策略**：单元测试、集成测试、E2E 测试覆盖点
4. **DB migration SQL**：完整的 schema 变更和数据迁移脚本

预计总工作量：**7 天**（Phase 1-4）
