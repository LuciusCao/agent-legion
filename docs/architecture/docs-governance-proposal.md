# 文档漂移治理方案：现行文档退役术语基线（proposal）

状态：**提案**（未实施；评审通过后按 §6 落地，落地时本文改判「已实施」并瘦身）
提出背景：develop→main release train #360（2026-09-01）的文档 review——合并点现行文档
（非时点快照）存在退役术语残留：`docs/architecture/README.md` 总览图写
"pipeline nodes"（`server/app/pipeline/` 已于 5dac451b 退役）、"external skills (git,
locked)"（#322 后 skill 是 skill root 下本地 in-place git 仓库，pin 锁在 DB
`skill_lock`）、技术栈漏 TanStack Query 且 "React Flow" 实为 `@xyflow/react`；
README/README_EN 的 skill 描述同样残留"外部 skill + 锁定 commit"旧语义。本 PR
（本方案随行）已修复这些存量；本方案解决的是**复发机制**。

## 1. 问题定性

现有文档防线只护得住一部分面：

| 现有机制 | 覆盖 | 盲区 |
|---|---|---|
| `generate_architecture.py --check`（freshness 门禁，挂 check-quick/check.sh/CI） | backend.md/frontend.md 等 AUTO-GENERATED API Surface 段 vs AST | **全部散文**：总览图、关键规则、Quick Start、表格非生成区 |
| `docs/README.md` 维护规则 + architecture/README.md 双索引登记 | 新增文档入索引（约定级，无门禁） | 现行文档正文里的旧概念表述 |
| 时点快照 banner 体系 | 历史文档豁免 | 现行/历史分类本身靠人维护 |

#360 的教训：入口文档（architecture/README 总览图）与权威文档（backend.md）自相矛盾
数周，freshness 门禁全绿——因为图不是生成物。**每个大 PR 退役一批概念**（#360 就
退役了 executor 定义、openclaw runtime、skill 源注册表、worker yaml 种子、全局
register token），而"退役公告写了"不等于"所有现行文档都改了"。

## 2. 方案：`docs_retired_terms` 检查（退役术语基线，ratchet 只降不升）

完全复用仓库既有治理模式（同 `sql_placeholders.py` / `service_data_boundary.py`
的形状），不引入新基础设施。

### 2.1 检查语义

- **扫描对象**：`CURRENT_DOCS` 白名单（见 §2.3）内的 markdown 正文。
- **违规定义**：白名单文档中出现 `RETIRED_TERMS` 表里的禁用模式（regex，带
  `re.IGNORECASE`）。
- **豁免语义**：命中行的上下文出现"退役表述词"（`已退役|已随|不再|退役|retired|
  removed|已删除|历史|legacy`，前后同句或紧邻 1 行）即视为**合法引用**——文档本来就
  需要写"#322 起注册表已退役"这类话。这与 `broad_except_audit.py` 的"带审计注释即
  放行"同构。
- **历史文档天然豁免**：只扫白名单，`risk-review-*`、`velites-poc-report` 等时点
  快照与 `docs/reviews/` 不在名单内。

### 2.2 数据文件：`config/architecture/docs-retired-terms.yaml`

选 YAML 而非 JSON：术语表需要注释讲清"何时退役、替代说法"，这是给人维护的文件。
与 `architecture-exemptions.yaml` 同风格。首版内容（按 #360 实际退役项）：

```yaml
# 现行文档禁用的退役术语。新增退役项 = 概念退役 PR 必须同步追加条目 +
# 清理现行文档命中（豁免语义见 scripts/architecture/docs_retired_terms.py）。
terms:
  - pattern: 'server/app/pipeline'
    retired_in: 5dac451b
    note: 视频流水线模块已删，非 agent 节点是 workflow code nodes
  - pattern: '\bexternal skills?\b'
    retired_in: '#322'
    note: skill 是 skill root 下本地 in-place git 仓库；写 "skills (local in-place git)"
  - pattern: '\bexecutor (definition|binding|allocation)s?\b'
    retired_in: '#284 / schema v47'
    note: executor 概念退役；模块名 executors/leases.py 与 worker/executor.py 合法
  - pattern: '\bopenclaw\b'
    retired_in: '#75'
    note: runtime 整体退役（pi / velites 两个）
  - pattern: 'config/(app|workflow|agent_legion)\.yaml'
    retired_in: split yaml 退役
    note: 文件存在即启动报错；有效配置 = 代码默认 + env + DB 实例设置
  - pattern: 'config/skills\.(yaml|lock)'
    retired_in: '#322'
    note: skill 锁在 DB global_settings 的 skill_lock 文档
  - pattern: 'agent-worker\.example\.yaml'
    retired_in: '#323'
    note: worker 唯一生效配置是状态副本 data/agent-worker-service/worker.yaml
  - pattern: 'AGENT_LEGION_WORKER_REGISTER_TOKEN'
    retired_in: '#35'
    note: 全局 register token 退役，改 workspace-scoped token
exemptions: []   # 与 file_budget 豁免同构的逃生通道：{path, term, reason, remove_when}
```

**注意**：`\bexecutor\b` 单词本身**不能**入表——`executors/` 包、`worker/executor.py`
模块、`executors/leases.py` 都是活代码路径，docs/data-layout.md 等正当引用它们。
只禁"概念性"组合词（definition/binding/allocation），这正是 regex 而非裸字符串
的意义。首版落库前用 §2.4 的 dry-run 定稿 pattern 集。

### 2.3 白名单：`CURRENT_DOCS`（写死在检查模块里，同 `_SCAN_ROOTS` 先例）

```python
_CURRENT_DOCS = (
    "README.md", "README_EN.md", "AGENTS.md",
    "docs/README.md",
    "docs/architecture/README.md", "docs/architecture/backend.md",
    "docs/architecture/frontend.md", "docs/architecture/deployment.md",
    "docs/architecture/project-structure.md",
    "docs/architecture/local-quality-gates.md",
    "docs/architecture/velites-harness.md",
    "docs/architecture/velites-model-registry.md",
    "docs/architecture/workspace-executor-evidence-matrix.md",
    "docs/architecture/node-sdk-and-worker-execution-design.md",
    "docs/architecture/materials-and-runs-design.md",
    "docs/agent-worker-deployment.md", "docs/data-layout.md",
    "docs/materials-storage-deployment.md", "docs/postgresql-runbook.md",
    "docs/remote-execution-runbook.md", "docs/studio-agent-mcp.md",
    "scripts/README.md", "examples/README.md",
)
```

**白名单维护规则（codex review #364 P2 采纳后固化）**：`_CURRENT_DOCS` 必须与
`docs/architecture/README.md` "现行文档"索引表保持同步——索引表新增/移除现行文档时
同步改白名单（§3 的索引对账检查落地后由检查强制）。两份带实施状态 banner 的设计
文档（`node-sdk-and-worker-execution-design.md`、`materials-and-runs-design.md`）
按索引表归类属现行文档，已入白名单；dry-run 实测两者当前零违规。

**CHANGELOG.md 不入白名单**（本方案 dry-run 的实测结论）：它的各版本段落按定义
描述"当时发生的变化"，退役项天然高频合法出现（"openclaw runtime 整体退役"、
"leftover `AGENT_LEGION_WORKER_REGISTER_TOKEN` env"），豁免语义拦不住也不该拦——
changelog 不是现行状态文档。

`server/app/mcp_server/*.md`、`server/app/studio_chat/*.md` 这类"代码内嵌 agent
playbook"暂不入白名单（它们随 feature PR 高频重写、且发布节奏独立）；首版先护住
人读的入口层，后续按需扩。

### 2.4 实施步骤（一个 PR，预计 0.5 天）

1. `scripts/architecture/docs_retired_terms.py`：实现 §2.1 语义；带 `--update-baseline`
   子命令输出当前命中清单（首版 dry-run 用来定稿 pattern 集，避免 pattern 误伤）。
2. `config/architecture/docs-retired-terms.yaml`：首版术语表（§2.2）。
3. `tests/scripts/test_architecture_docs_retired_terms.py`：同
   `test_architecture_sql_placeholders.py` 的夹具法——构造临时文档树覆盖
   命中/豁免/白名单外三种路径。
4. `scripts/architecture/repository.py` 末尾挂
   `errors.extend(check_docs_retired_terms(root))`（与 video_legacy/sql_placeholders
   并列）。
5. 随行清理存量命中（本 proposal 随行 PR 已清了 5 处，落地 PR 复查一遍应为零）。
6. `docs/architecture/README.md` 索引表登记本文；AGENTS.md §5 加一行：
   「概念退役 PR 必须同步在 `config/architecture/docs-retired-terms.yaml` 追加条目
   并清零现行文档命中」（该文件加入 pre-push lane 判定的共享文件清单，改动它自动
   全量门禁——已有机制，无需新配置）。

### 2.5 为什么不做"更强的"方案（备选与否决理由）

- **语义/LLM 校验文档 vs 代码**：误报率不可控、CI 依赖模型、成本与确定性都差；
  这个仓库的门禁哲学是"确定性的、可 ratchet 的"，LLM 校验违背它。
- **全文 freshness（每段落带哈希）**：维护成本转嫁给每次合法编辑，噪音远大于收益。
- **只靠 release-train 前的 review checklist**：#360 证明人会漏——入口图漂移正是
  从"公告写了、正文没改"漏过去的；确定性检查兜底。
- 术语基线是**最小可行面**：只拦"已被退役的概念再次以现行语气出现"这一类最伤人的
  漂移（读者照着不存在的路径/机制操作），不追求拦截所有表述漂移——后者本来就该靠
  人 review。

## 3. 配套小项（可选，随手做）

- `docs/README.md` 的双索引登记从约定升级为检查：白名单文档集合 vs
  `docs/architecture/README.md` 索引表 diff（新增 .md 未登记即 error）。实现 20 行，
  并入 `docs_retired_terms.py` 同 PR。
- pre-push 的 docs-only lane 已经只跑静态检查（AGENTS.md §4），新检查挂在
  `check_architecture.py` 内自动继承该 lane，**不增加** docs-only PR 的 CI 时长
  （纯文本扫描 <100ms）。

## 4. 维护纪律（与既有治理一致）

- 退役概念 → PR 必须追加术语条目（AGENTS.md 红线，同 invariant registry 义务）。
- 误伤 → 走 `exemptions`（带 `remove_when`），不许删 pattern。
- pattern 只增不删；确需删除（概念复活）须在 yaml 注释里记录 issue 依据，
  同 budgets JSON 的 git 锚点纪律。

## 5. Quality Impact

按 AGENTS.md §5「spec / plan 必须包含 Quality Impact 小节」的要求补齐（codex review
#364 P1）：

- **gate 时长**：新增检查是纯文本 regex 扫描（23 个白名单文档 × 8 条 pattern，
  实测量级 <100ms），挂 `check_repository` 静态段，对 quick/full/CI 各 lane 的
  时长影响不可测。docs-only PR 不增加 lane（AGENTS.md §4 的路径裁剪机制天然覆盖）。
- **误报面**：三层收窄——regex 只禁概念组合词（`executor definition/binding/
  allocation`）不禁活代码路径（`executors/` 包、`worker/executor.py`）；命中行
  上下文含退役表述词即放行（同 `broad_except_audit.py` 审计注释语义）；CHANGELOG
  与时点快照不在白名单。首版 pattern 已对全仓 dry-run 实测：23 文档 0 违规、
  唯一误伤面（CHANGELOG 的历史段落）已通过移出白名单消除。残余误报走
  `exemptions`（带 `remove_when`），不许删 pattern。
- **测试范围**：新增 `tests/scripts/test_architecture_docs_retired_terms.py`
  （夹具法：命中/豁免/白名单外三路径，同 `test_architecture_sql_placeholders.py`
  形状）；纯静态、`@pytest.mark.no_db`，进 smoke 层，不触碰 TRUNCATE 隔离。
- **维护成本**：每次概念退役 PR 多两步（yaml 追加条目 + 清零命中），与 invariant
  registry 的既有义务同量级；pattern 只增不删，删除须记 issue 依据（同 budgets
  JSON 的 git 锚点纪律）。豁免到期检测复用 nightly 的 `exemption-expiry` job
  （读同一 yaml 的 `remove_when`，机制已存在）。

## 6. 验收标准

- `uv run python -m scripts.check_architecture` 在含一个故意违规 fixture 的
  worktree 上红、干净树上绿。
- `tests/scripts/test_architecture_docs_retired_terms.py` 进 smoke 层。
- 本文件改判「已实施」，方法段瘦身、保留术语表维护指引。
