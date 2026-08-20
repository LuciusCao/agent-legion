# Custom Workflow Nodes（DB-backed 节点代码）设计草案

状态：已实现（M1–M3）；**#96（2026-08-17）已退役「内置节点 path 绑定」**：
capability 不再声明 `path`，所有节点代码（含 demo 两个节点）统一以 DB
发布文本在 velites 沙箱执行；demo 节点的出厂代码在 workspace 初始化时写入
workspace 作用域（`services/demo_node_seed.py`），旧 global 记录只作为一次性
迁移输入和历史 replay 数据，
`workflow_nodes/` 只剩这两个 git 评审的种子源文件。本文 §2 的双类治理表、
解析优先级第 3 级「内置路径」、§6/§7 中基于 path 的表述均为历史记录；
现行语义以 `config/architecture/architecture-invariants.yaml` 的
EXEC-CODE-001/002/003 为准。**P-0.5（2026-08-17，schema v47）**：executor
定义 / allocation / binding 概念整体退役（两表 drop），非 Agent 路由节点一律
进隐含 code 池，节点 `config_schema:` 块成为 code 节点唯一的参数声明层
（executor 兜底已删）；本文 §2 解析优先级与绑定链中经 executor 定义的表述
均为历史记录，现行语义以 EXEC-CODE-POOL-001 / CONFIG-MANIFEST-001 为准。
日期：2026-08-04
关联：EXEC-CODE-001、CONFIG-MANIFEST-001、VAULT-SECRET-001

## 1. 背景与目标

内置 agent 功能规划：用户描述业务流程，agent 生成 workflow；用户再针对单个 node
描述打磨，由 agent 编辑节点代码。这意味着一部分节点代码从「repo 里 review 过的
资产」变成「用户数据」，现有两条假设不再成立：

- EXEC-CODE-001 假设节点代码永远 git-reviewed、CI-gated；
- code executor 假设 `capability → 代码文件路径` 的映射启动时从
  `config/workflow.yaml` 静态加载（`executors/code.py:124-136`）。

目标：

1. 节点代码分两类治理：**内置节点**（repo-tracked，维持 EXEC-CODE-001）与
   **自定义节点**（DB 存储、版本化、走发布流）。
2. 自定义节点代码可被 agent（或用户）在线编辑，带审计与回滚。
3. 已在运行的 workspace 完全兼容：现有 active revision 只引用内置节点，绑定链
   （`workspace_node_bindings` → executor → yaml path）不变，无需数据迁移。
4. 运行中的 job 不受后续代码编辑影响（代码版本随 job 冻结）。

非目标（本期不做）：

- agent 生成 workflow 的编排 UI 与会话协议（仅预留 `created_by` / 审计字段）。
- 自定义节点的 OS 级沙箱（见 §7，列为后续阶段）。
- 自定义节点的跨 workspace 共享 / 市场分发。

## 2. 节点分类与解析优先级

| | 内置节点 | 自定义节点 |
|---|---|---|
| 代码位置 | `workflow_nodes/*.py`（git） | DB 表（见 §3） |
| 变更通道 | git PR + CI | 在线编辑 → 发布流 |
| 治理 | EXEC-CODE-001 | 新 invariant（见 §8） |
| 执行隔离 | 子进程 | 子进程（本期），OS 沙箱（后续） |

dispatch 时的代码解析顺序（高优先级在前）：

1. job 快照冻结的自定义代码版本（运行中的 job）；
2. 该节点已发布（published）的自定义代码版本；
3. 内置路径（yaml executor definitions）。

「自定义化」一个内置节点 = fork：把内置代码复制为自定义 v1，绑定到该
`(workspace, workflow, node)`，之后该节点走自定义解析；删除自定义代码（或全部
版本 archived）即回落内置实现。

## 3. 存储设计

新表（migration 挂载方式遵循 `server/app/db/migrations/` 既有约定）：

```sql
create table if not exists workflow_node_codes (
  id            text primary key,           -- uuid
  workspace_id  text not null references workspaces(id),
  workflow_key  text not null,
  node_key      text not null,
  version       integer not null,
  status        text not null check (status in ('draft','published','archived')),
  code          text not null,
  code_hash     text not null,              -- sha256，对齐 definition_hash 模式
  created_by    text not null,              -- user:<id> | agent:<session_id>
  change_note   text,                       -- 用户描述 / agent 摘要
  created_at    timestamptz not null default now(),
  published_at  timestamptz,
  unique (workspace_id, workflow_key, node_key, version)
);
-- 每个 (workspace, workflow, node) 至多一行 published（部分唯一索引）
create unique index if not exists workflow_node_codes_published
  on workflow_node_codes (workspace_id, workflow_key, node_key)
  where status = 'published';
```

设计要点：

- **不可变版本 + status 流转**（draft → published → archived），与
  `workflow_revisions` 的语义对齐；rollback = 把旧版本重新 publish（产生新
  version 或复用「激活指针」语义，实现时二选一，倾向重新 publish 保持不可变）。
- 同一节点的并发编辑以 `(workspace, workflow, node, version)` 唯一约束 +
  `next_version = max+1` 串行化（同 `workflow_revisions` 的
  `next_workflow_revision_version` 模式）。
- 审计信息（`created_by`、`change_note`）随版本天然留存，不需要独立 audit 表
  （仓库目前也没有通用 audit 表）。
- `code` 直接存 text；若未来单文件超 1MB 再考虑接入 `artifacts` 内容寻址存储。

## 4. 编辑与发布流

API（workspace 作用域，鉴权走 `require_workspace_access`，非 admin-only——这是
用户数据而非 repo 文件）：

- `GET  /workspaces/{id}/workflows/{wf}/nodes/{node}/code`
  → 当前生效代码 + 来源标记（`origin: builtin|custom`）+ published version。
- `PUT  .../code` → 以当前生效代码为基线创建新 draft 版本（或覆盖既有 draft）。
- `POST .../code/publish` → 校验后 draft → published（旧 published 自动
  archived），记录 `published_at`。
- `GET  .../code/versions` → 版本列表（含 created_by / change_note）。
- `POST .../code/rollback` → 指定旧版本重新 publish。
- `DELETE .../code` → 全部版本 archived，节点回落内置实现。

保存时校验（service 层，route 只做编排）：

- AST 解析通过；模块级存在 `run` callable（与现有
  `routes/workflow_node_files.py` 的写入校验同一套 helper，抽到 service 复用）；
- 大小上限（建议 64KB，超出即拒绝——自定义节点应保持单文件内聚）；
- **不做** import 白名单之类的静态内容过滤（可绕过，虚假安全感）；不受信问题
  由执行隔离解决（§7）。

与现有发布流的关系：节点代码版本独立于 workflow revision 版本。workflow
revision 管 DAG 结构与节点声明，节点代码管单个 capability 的实现——两者解耦，
避免每次打磨节点都产生新 workflow revision。job intake 时把当前 published
`code_hash`/`version` 冻结进 `job_batches.source_payload_json["node_config"]`
同级的快照字段（复用 `resolve_workflow_node_configs` 的冻结时点），dispatch
优先读冻结版本。

**版本关联（pins）**：独立演进不等于无关联。workflow revision publish 时把当
时各节点的 published 代码版本快照进 revision（`definition_json` 旁挂
`node_code_pins`，或独立列）。这样任意时刻都能回答「workflow vN 发布时搭配的
是哪些节点代码版本」，workflow 回滚（重新 publish 旧 YAML）时可按旧 revision
的 pins 把节点代码一并 revert 到对应版本。注意本期 pins **只写不读**：它是可
回溯的搭配记录，revert 是人工（或后续工具）按 pins 逐节点回滚的操作，不是
publish 时的自动行为。

**为什么不采用「版本区间绑定」**（如 workflow v1 兼容 node v1–v3）：兼容性区
间需要机器可校验的判定依据才有意义，而节点代码的行为兼容性（读哪些上游产
物、写哪些文件）无法静态检查，区间只能靠人肉维护——必然腐化，且提供虚假信
心。真正可执行的防线是：

1. 节点的接口（capability + inputs/outputs + config_schema）本来就声明在
   workflow definition 里；接口不变，拓扑变化不影响节点代码；接口变了，本
   质是 breaking change，应显式升级节点代码并冒烟验证，而不是靠区间元数据
   兜住；
2. pins 提供「可回溯的搭配记录」，回滚不错配；
3. job 冻结提供「在跑任务不受任何后续变更影响」。

三者合起来覆盖了版本区间想解决的问题，成本远低于维护区间元数据。

## 5. 执行链改动

改动集中在 code executor 的代码装载环节，绑定链其余部分不动：

- `CodeExecutor.__init__` 的启动时路径校验（`code.py:124-136`）与
  `validate_code_config_paths` 只覆盖内置 capability，不变。
- `_load_run_callable`（`code.py:33-47`）扩展为两个来源：
  - 内置：按路径 import（现状）；
  - 自定义：从 DB 取 code 文本，`importlib.util.spec_from_loader` 从字符串构
    模块执行。DB 读取发生在子进程内（`_run_code_node` 已重建 DB 连接，沿用该
    模式），避免父进程缓存旧代码。
- dispatch 时 `ExecutionContext` 增加 `code_ref`（frozen version / published
  version / None=builtin），由 `schedule.py` 在 `dispatch_effective_config`
  同一时点解析。注意 `routing.py:26` 的 30s 路由缓存只管 executor 绑定，不含
  代码版本——代码版本解析要么不缓存，要么用同样的短 TTL。
- 契约不变：自定义代码同样暴露 `run(job, job_dir, runtime)`，
  `runtime["node_config"]`、secret 解析、manifest 白名单全部沿用。

## 6. 对现有 Studio 在线编辑的收缩

commit 5f6e62e4 引入的 `PUT /api/workflow-nodes/files/{path}`（直接改写 repo
文件、保存即生效）与本设计冲突，且违反 EXEC-CODE-001 的声明：

- **删除写端点**；`GET` 保留为只读查看（内置节点代码对 workspace 成员可见，
  作为 fork 的起点）。
- 前端 `WorkflowNodeCodeSection` 改为只读视图 + 「fork 为自定义节点」入口
  （调用 §4 的 PUT 创建自定义 v1）。
- config 编辑（`WorkflowNodeConfigSection` / `config_schema` 体系）不受影响。

兼容承诺：当前两个在运行 workspace 仅使用内置节点，绑定链、yaml 路径、
revision、job 快照语义全部不变，零迁移。

## 7. 执行隔离分层（关键风险）

自定义代码（agent 生成）是不完全受信输入，而当前 code executor 的子进程隔离
（`executors/code.py` 的 multiprocessing）只防崩溃不防恶意：子进程与 server
同用户同权限，可以读 server 的文件系统（含 `.env`、vault master key、DB 连接
串）、发起任意网络请求。分三期：

- **本期（MVP）**：沿用子进程隔离，叠加发布权限收敛（workspace 内指定角色才
  能 publish）+ 全量审计 + 代码大小限制。
  - 「受控环境」的含义：部署方认可「能发布自定义节点代码的人 ≈ 有 server
    权限的人」这一信任假设。典型即自托管、workspace 编辑者全是自己团队的
    环境。对多租户 SaaS、编辑者是外部客户的环境，MVP 级别**不够**——一条
    幻觉或被诱导的 agent 输出就能把 vault 明文拖走。
  - 配置 gate 保留，但**当前阶段默认开启**：目前无真实用户、环境完全受控
    （自托管、编辑者均为团队成员），满足上述信任假设；未来面向外部用户
    的部署形态出现时再翻回默认关闭。
- **二期（OS 级沙箱）**：复用 velites 已验证的隔离机制——macOS 用
  `sandbox-exec` + seatbelt profile（`deny default`，白名单读系统路径，写仅
  工作目录/$TMPDIR），Linux 用 bubblewrap（`/` 只读 bind、tmpfs `/tmp`、仅工
  作目录可写）。目前这套只包裹 velites harness 内 bash 工具的子进程
  （`velites/src/sandbox.rs`、`tools/bash.rs`）；二期把 profile 生成与进程包
  裹抽成可复用模块，套到 code 节点子进程上：文件系统只读 + `job_dir` 可写，
  网络默认拒绝（或按 capability 白名单域名，如 CMS）。沿用 velites 的
  fail-closed 原则：探测不到沙箱后端就拒绝执行自定义节点（内置节点不受影
  响）。沙箱就绪前不对外开放在线编辑。
- **二期落地要点**（M3 实际交付，细节见代码）：`velites sandbox wrap` 单一策
  略源；子进程 env 白名单（PATH/TMPDIR/HOME/LANG/LC_*/PYTHONPATH）；macOS 读
  根收窄到 server/workflow_nodes/config + 父目录 list-only grant；Linux 选择
  性只读 bind + `--unshare-pid`/私有 /proc；signal 收紧为 `(target self)`；
  结果通道 JSON + 严格 schema（不用 pickle）；payload 走 stdin（secret 不落
  盘）；取消/超时杀整个进程组。
- **三期**：资源限额（子进程 `setrlimit` CPU/内存/打开文件数）、secret 按节
  点白名单注入（当前 `resolve_secret_refs` 会把该节点 config 里的 secret 全
  部解密注入，自定义节点应只允许声明过的 key）。

## 8. Quality Impact

- **新 invariant**（建议 `EXEC-CODE-002`）：自定义节点代码只存 DB、经发布流
  生效、版本不可变、job 冻结代码版本；禁止任何运行时改写 repo
  `workflow_nodes/` 的路径（含删除 §6 的写端点）。同步登记
  `config/architecture/architecture-invariants.yaml` 与 evidence matrix。
- **EXEC-CODE-001 不变**，适用范围收窄为内置节点，文案需同步澄清。
- **新迁移**：`workflow_node_codes` 表 + `SCHEMA_VERSION` bump，按
  `migrations/` 包约定挂载，幂等。
- **测试**：迁移测试（`tests/db/`）、发布/回滚/回落内置的 service 测试、
  dispatch 冻结版本优先级的 worker 测试、API 契约测试（response model 齐
  全）；新增 API 出参不得泄露 secret（VAULT-SECRET-001）与非白名单 config
  （CONFIG-MANIFEST-001）。
- **预算**：新 route/service/migration 文件遵守行数 ceiling；前端
  `WorkflowNodeCodeSection` 改造纳入 frontend 预算。
- **AGENTS.md**：Boundary Rules 增补「节点代码变更通道」条款（内置走 git，
  自定义走发布流，禁止第三条路）。

## 9. SCHEMA_VERSION 跳号说明与对策

现状（`server/app/db/schema.py`）：develop 已到 22，本分支 `SCHEMA_VERSION`
= 25（22 code_executor_bindings / 23 local_executor_removal / 24 node_cms_config /
25 custom_node_codes，均为本分支迁移）；升级判定是「`schema_migrations` 里没有
version=25 这一行就整体重放全部幂等迁移」，且重放后只插入
`(25, "custom_node_codes")` 一行。

风险：若另一个分支用 25（或 22–25 任一已被本分支占用的号）先合并并在某环
境升级过，该环境的 `schema_migrations` 已存在 25，本分支的迁移会被整体跳过
——且没有任何报错。反之亦然。

**历史低版本库的升级不需要专门处理**：升级判定只看「当前 SCHEMA_VERSION 的
行是否存在」，与库里盖的是哪个旧版本号无关。例如本 worktree 开发库盖的 19、
develop 环境盖的 21，在本分支代码启动时都会触发整体重放——`postgres_schema.sql`
是全量幂等 DDL，各迁移函数自身幂等，重放即收敛到当前状态（本分支 tests/db 已
实际验证过 v19 旧库重放成功）。真正的隐患只有同号碰撞（上段），不是跨版本链。

对策（合入前必做）：

1. 与所有在途分支对齐各自占用的版本号，本设计的新迁移取对齐后的下一个空闲
   号；
2. 中期应把升级判定从「单行存在」改为「逐迁移记录、缺哪个补哪个」
   （`schema_migrations` 按迁移名记录而非单版本号），消除分支间版本号耦合。
   这超出本草案范围，单独立项。

## 10. 现有两个 workspace 的迁移方案

背景：两个在运行 workspace 当前只用内置节点，本分支的迁移已把它们的 CMS 凭据
迁入 vault。它们**现在不需要任何动作**；以下方案在「M2 上线后」或「任一
workspace 首次需要修改节点」时（取其早者）执行。

1. **盘点**：确认是否存在经 Studio 写端点在线改过、与 git HEAD 不一致的
   `workflow_nodes/*.py`（逐文件 diff 部署目录与仓库）。这是唯一的隐式状态
   ——一旦 M1 删掉写端点，这些改动就成了「不在 git 里、也不在 DB 里」的孤儿。
2. **冻结窗口**：公告暂停节点编辑；等待 in-flight job 排空（或确认可接受
   中断重跑）。
3. **导入孤儿改动**（仅当第 1 步有发现）：把线上文件内容作为自定义 v1 写入
   `workflow_node_codes`（`created_by` 记实际操作者，`change_note` 记
   "migrated from online edit"），publish；随后把部署目录的文件恢复为 git
   版本，消除漂移。
4. **凭据收尾**：确认 vault master key 已就位；对迁移期落入
   `node_config_json` 的明文 token（`_store_plaintext_token` 兼容窗口）执行
   一次性重加密，之后关闭明文透传路径（VAULT-SECRET-001 收尾）。
5. **验证**：每个 workspace 跑一个端到端冒烟 job，核对 dispatch 路由、节点
   产出与 CMS 连通性；确认回退路径可用（archived 全部自定义版本即回落内置
   实现）。
6. **legacy 清理**（显式推迟，未随 M3 完成）：下线
   `workspaces.resource_config_json` 列及前端 `resource_config` 残留类型。

回退策略：迁移本身只新增 DB 行、不改绑定链；若冒烟失败，删除/归档自定义版
本即回到内置实现，repo 文件恢复由 git 保证。

## 11. 里程碑建议

1. M1：§6 收缩（删写端点、前端只读化）——可随当前 feature 分支合入。
2. M2：§3 表 + §4 发布流（含 node_code_pins）+ §5 执行链（MVP 隔离级别，
   配置 gate 当前阶段默认开启）——受控环境可用；同步执行 §10 迁移。
3. M3：§7 二期沙箱——**纳入本版本范围**（当前版本交付即包含，不作为后续
   版本）；对外开放在线编辑的前置条件。
4. M4：agent 编辑入口（created_by=agent:*、change_note 记录用户描述）——
   对接内置 agent 功能。
