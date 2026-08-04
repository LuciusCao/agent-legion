# velites 升格为一级 Runtime 实施计划

状态：**已落地**（Phase 1：PR #20；Phase 2：PR #21 + 审题链路迁移 2026-08-03；金丝雀关闭 `14ec130f` 2026-08-04；Phase 3 阶段 B 文档收口随本状态更新提交）。阶段 C（pi 退役）另立项未排期。
范围：`server/app/agent_broker/`、`server/app/agent_catalog.py`、`server/app/routes/`、`worker/`、`config/`、`frontend/src/generated/`
关联文档：[velites-harness.md](velites-harness.md)（harness 设计）、[workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)（证据矩阵）、`config/architecture/architecture-invariants.yaml`（invariant registry）

## 1. 背景与目标

> 本节与 §2 描述的是**实施前基线**（2026-08-02 核实），作为改动锚点存档保留；落地后的当前状态见 velites-harness.md §9。

velites（`velites/` Rust agent harness）已通过金丝雀验证：一天 4.3 万生产节点、99.6% 成功率、96 并发。当前它以 `workflows.pi.flavor: pi|velites` 全局开关（`config/workflow.yaml:120`）的方式作为 pi runtime 的"实现 flavor"存在——所有 agent 定义仍是 `runtime: pi`（`config/workflow.yaml:1-46`），flavor 决定 Host 为节点生成哪套命令行。

架构方向：未来支持多种 agent 类型，**pi、openclaw、velites 是平级 runtime**，flavor 只是灰度过渡层。本计划覆盖把 velites 从 flavor 升格为 `AgentDefinition.runtime` 一等值的全部改动，以及 flavor 的三阶段退役路线。

目标：

- `runtime: velites` 成为合法 agent 定义值，dispatch / claim / sweeper / Worker / UI 全链路支持；
- per-agent 灰度：单个 agent 定义可独立迁到 velites 或迁回 pi，互不干扰；
- `runtime: pi` 的既有行为（含 flavor 全局开关与一键回退）在过渡期逐比特不变；
- 金丝雀验证过的回退语义在 runtime 模型下有明确等价物与事故预案。

非目标：

- openclaw runtime 的 agent 分发实现（dispatch 对其继续 fail-fast）；
- pi 本体退役（仅给出 flavor 删除的前置条件与路线，执行另立项）；
- velites 事件 schema 演进（wire 兼容契约不变，见 §2.8）。

## 2. 现状锚点（实施前基线，2026-08-02 已核实）

### 2.1 runtime 枚举校验（当前只认 `{"pi","openclaw"}`）

- `server/app/agent_catalog.py:22` — `AgentDefinition.runtime: Literal["pi", "openclaw"]`，pydantic 层 fail-fast。定义权威是 yaml：`config/workflow.yaml:1-46` 的 `agents:` 段 → `server/app/settings.py:222` `load_agent_definitions` → `server/app/main.py:84` `sync_agent_definitions` 落 `agent_definitions` 表（immutable snapshot，`definition_hash` 含 runtime，`agent_catalog.py:49-56`）。
- `server/app/agent_workers.py:57-61` — Host 侧 Worker 注册 `runtimes` 白名单校验。
- `server/app/routes/agent_workers_contracts.py:13` — 注册契约 `runtimes: list[str]` 为自由字符串，枚举校验在 registry 层，契约本身无需改。
- `server/app/routes/agent_catalog_contracts.py:8` — catalog 出参 `runtime: Literal["pi","openclaw"]`，生成到 `frontend/src/generated/api.ts:1483`（前端唯一的 runtime 字面量枚举；`runtimes` 字段 api.ts:1596/2446 为自由 `string[]`）。
- `worker/config_store.py:80-81` — Worker 本地配置白名单校验；默认值 `["pi"]`（`worker/config_store.py:40`）。

### 2.2 dispatch 硬编码与命令链

- `server/app/agent_broker/dispatch.py:54-55` — `definition.runtime != "pi"` 直接 `raise ValueError("... not implemented yet")`。
- `dispatch.py:59` 读全局 `PiConfig.from_runtime(settings.executor_runtime.workflows.pi)`；`dispatch.py:79-87` 把 `binary/flavor/provider/model/thinking/timeout_seconds/velites_no_sandbox` 冻结进 `manifest["pi"]`；`dispatch.py:68` 写 `manifest["runtime"] = definition.runtime`；`dispatch.py:113` `render_command_spec(manifest)`。
- 命令链：`server/app/workflows/pi_protocol.py:108-129` `render_command_spec` → `server/app/workflows/velites_command.py:38-64` `build_command_for_flavor` 按 `manifest["pi"]["flavor"]` 分发到 `build_velites_command`（velites_command.py:67-112）或 `pi_fallback`（pi_protocol.py:73-105 `build_command`）；未知 flavor fail-fast（velites_command.py:64）。
- claim 时重渲染：`server/app/agent_claim_compatibility.py:21-40` `live_claim_manifest` 用 manifest 内冻结的 `pi` 块叠加 revision 的 provider/model/thinking 覆盖后再次 `render_command_spec`（:38-39）。**manifest 冻结的 flavor 即权威，重渲染自动一致。**
- `server/app/services/execution_catalog_projection.py:46` — `if definition.runtime == "pi"` 才把 provider/model/thinking 投影进执行目录。

### 2.3 claim 匹配

- `server/app/agent_broker/claim.py:59` Worker `runtimes_json` 解析为集合；候选查询 join `agent_definitions d` 取 `d.runtime`（claim.py:96）；`claim.py:127` `selected["runtime"] not in runtimes` 跳过。**runtime 匹配逻辑本身已通用，扩枚举即生效，claim 路径零代码改动。**
- `server/app/agent_claim_compatibility.py:43-54` `worker_can_run` 只看 capability 与 (provider, model)，无 runtime 维度——保持如此，runtime 在 claim.py 单点判断。

### 2.4 unclaimable sweeper（现有缺口）

- `server/app/agent_broker/unclaimable.py:42-52` 只聚合非 revoked Worker 的 capabilities/models；`_unmatched_reasons`（unclaimable.py:112-138）不探测 runtime。
- 当前无危害（所有定义都是 `runtime: pi`，所有 Worker 都声明 pi）。一旦存在 `runtime: velites` 定义而无 Worker 声明 velites，请求永久 queued——与该 sweeper 要消除的 queue-rot 同一失败模式。**必须与枚举扩展同阶段修复。**
- 零非 revoked Worker 时不动作的保护（unclaimable.py:38-39 注释）保持不变。

### 2.5 Worker 侧

- 执行天然 flavor/runtime 无关：`worker/execution_prepare.py:78-87` 对 `command_spec["command"]` 做 `{job_dir}` 等占位符替换，`worker/executor.py:115-116` 直接 `Popen(command)`。Worker 不需要认识 velites，只需要 PATH 上有 argv[0] 的二进制。
- 声明链：`worker/config_store.py:40,80` → `worker/host_client.py:70` 注册上送 → Host `agent_workers.py:57-61` 校验落库。
- UI：`worker/ui/index.html:163-166` 两个 checkbox（pi / OpenClaw）；`worker/ui/app.js:143-144` 回填、`app.js:598` 提交 `data.getAll("runtimes")`——逻辑通用，加 checkbox 即可。
- 二进制分发现状：手工 `cargo build` + PATH，无交付物机制。

### 2.6 flavor 配置链

- `config/workflow.yaml:116-128` — `workflows.pi.flavor: velites`（当前生产值），:119 注释已写明"`flavor: pi` 单字段即回退"；`velites_no_sandbox` 逃生门同段。
- `server/app/executors/runtime_config.py:19-32` — `PiRuntimeConfig.flavor: Literal["pi","velites"]`，`_flavor_binary` 使 binary 默认值跟随 flavor。
- `server/app/workflows/pi_config.py:14,43-47` — `PiConfig.flavor` 与 `from_config` 同一套校验/归一化。

### 2.7 invariant 与证据

- `config/architecture/architecture-invariants.yaml`：EXEC-EVENT-SCHEMA-001（:478）、EXEC-HARNESS-ISOLATION-001（:487）、EXEC-HARNESS-BUDGET-001（:496）、EXEC-HARNESS-SANDBOX-001（:505）。四条均为 harness 本体 invariant，升格后措辞与证据不变。
- 证据矩阵 `docs/architecture/workspace-executor-evidence-matrix.md:47-50` 对应四行，状态 Verified。
- AGENTS.md §6 现有表述"harness flavor 切换只经 `workflows.pi.flavor` 配置"将随语义收窄过时，需在 Phase 2 同步。

### 2.8 事件契约（不变量）

velites 事件流保持 pi 消费子集 wire 兼容：Host 消费方 `server/app/services/pi_event_scan.py`、`token_usage.py`、`pi_model_error.py`、`job_log_renderer.py`；additive 扩展（message.timing）已存在。升格 runtime 不改事件 schema；`velites/schema/events.schema.json` 改动纪律（`cargo run --bin velites-schema -- schema/events.schema.json` + `velites/tests/schema_current.rs`、`golden_events.rs`）不变，EXEC-EVENT-SCHEMA-001 继续作护栏。

## 3. 目标模型

```
AgentDefinition.runtime = "pi"       → pi 协议族；实现由 workflows.pi.flavor 选择（pi 或 velites 二进制）
AgentDefinition.runtime = "velites"  → 钉死 velites 实现，忽略 flavor
AgentDefinition.runtime = "openclaw" → 未实现，dispatch fail-fast（现状保持）
```

过渡期核心语义（写入代码注释与本文档）：**flavor 只作用于 `runtime: pi` 的 agent**。`runtime: velites` 不受全局 flavor 影响——这正是平级模型相对全局开关的核心优势：灰度粒度从"整个部署"细化到"单个 agent 定义"。

## 4. 实施内容

### 4.1 runtime 枚举扩展

把 `"velites"` 加入全部白名单，逐点：

- `server/app/agent_catalog.py:22` — `Literal["pi", "openclaw", "velites"]`。
- `server/app/routes/agent_catalog_contracts.py:8` — 同步 Literal；随后重新 export_openapi 刷新 `frontend/src/generated/api.ts:1483`（纪律：前端 transport 类型只从生成文件派生，不手写）。
- `server/app/agent_workers.py:59-61` — 集合并更新报错文案（"runtimes must contain pi, openclaw and/or velites"）。
- `worker/config_store.py:80-81` — 集合并更新报错文案；默认值保持 `["pi"]`（声明 velites 是显式运维动作）。
- `server/app/routes/agent_workers_contracts.py` — 无需改（自由字符串）。
- 检查 `config/agent-worker.example.yaml`、`deploy/worker.company.example.yaml`、`deploy/worker.home.example.yaml` 的 runtimes 示例注释，补 velites 说明。

### 4.2 dispatch 放开

最小侵入方案：**dispatch 冻结 manifest 时按 runtime 覆写 flavor**，复用现有整条命令链与 claim 重渲染，不改 `build_command_for_flavor` 签名。

- `server/app/agent_broker/dispatch.py:54-55` 改为分派：
  - `runtime == "velites"`：在构建 `manifest["pi"]` 处（dispatch.py:79-87）强制 `flavor = "velites"`，`binary` 未显式配置时归一化为 `velites`（与 `runtime_config.py:31-32` `_flavor_binary` 同一规则），随后走原链——`render_command_spec` 经 `build_command_for_flavor` 产出 velites argv；`live_claim_manifest` 重渲染自动一致。
  - `runtime == "pi"`：现状逐比特不变（flavor 决定实现）。
  - 其他（openclaw）：保留 fail-fast，报错文案列出已支持集合。
- `server/app/services/execution_catalog_projection.py:46` — 条件放宽为 `runtime in ("pi", "velites")`（velites 同样需要 provider/model/thinking 投影）。
- manifest 的 `"pi"` 块暂不改名（改名是破坏性变更，留 Phase 3 与 command_spec version 升级一起做）。

### 4.3 claim 匹配与 unclaimable sweeper

- claim 路径（claim.py:59,96,127）零代码改动；补测试（§6）。
- `server/app/agent_broker/unclaimable.py`：
  - Worker 聚合（:42-52）增加 `runtimes_json` 并集；
  - `_unmatched_reasons`（:112-138）增加 runtime 探测，沿用"单维度万能声明探针"手法，reason 文案如 `runtime 'velites' not declared by any Worker`；
  - 零 Worker 保护不变。

### 4.4 agent 定义迁移策略与 per-agent 灰度

定义权威是 `config/workflow.yaml` `agents:` 段：迁移 = 把单个 agent 的 `runtime: pi` 改为 `runtime: velites`，重启后 `sync_agent_definitions`（agent_catalog.py:92-144）落库。

关键操作事实（同步进 `docs/remote-execution-runbook.md`）：

- 改 runtime 改变 `definition_hash`（agent_catalog.py:49-56）→ 产生新 definition revision；pinned 旧 hash 的 queued 请求被 `fail_stale_definition_requests` 判 stale 失败。**迁移应在低峰或排空队列后进行；被 stale 的 job 按既有 stale 语义重提。**
- claimed/running 中的执行不受影响：manifest 入队即冻结，Worker 按冻结 command_spec 跑完。
- capability 路由不受影响：Workspace Route 由 capability 派生，单定义约束（agent_catalog.py:73-89）不变，改 runtime 不动路由。
- Worker 舰队需先于定义迁移完成 velites 声明（§4.6），否则 sweeper 会把新请求判 unclaimable 失败——**顺序：Worker 声明先行，定义迁移随后。**
- 灰度顺序建议：先迁低风险小流量 agent（如 `video-subtitle-review-v1`），观察 ≥ 3 天；再迁 question 链路主力（`question-key-info-v1` 等）；最后全量。每步是一次单字段 yaml 改动 + 重启，独立可回退。

### 4.5 flavor 退役三阶段

- **阶段 A（过渡，本计划 Phase 1–2）**：flavor 仍是 runtime=pi agent 的全局实现开关；runtime=velites agent 的 manifest flavor 被 dispatch 钉死。两者并存。
- **阶段 B（已落地，2026-08-04）**：flavor 正式收窄为"runtime=pi agent 的实现选择层"，AGENTS.md §6 与 velites-harness.md §9 已改写。注意与原文假设的偏差：video_knowledge 4 个 agent 保持 `runtime: pi`（该链路暂无新内容产出，用户决策不迁），故 flavor 的实际消费者**仅剩这 4 个 video agent**而非"无消费者"——在它们迁出或下线前，flavor 仍是活跃实现路径，不能删除；新增 agent 一律直接声明 runtime。
- **阶段 C（pi 退役，另立项）**：删除 `PiRuntimeConfig.flavor`（runtime_config.py:19）、`PiConfig.flavor`（pi_config.py:14）、`build_command_for_flavor` 分发层（velites_command.py:38-64）、pi argv 构建 `build_command`（pi_protocol.py:73-105），manifest `"pi"` 块重命名为 runtime 中性名（command_spec `version` 升 2，Worker 占位符替换兼容处理）。触发条件：pi 二进制生产零调用 ≥ 一个季度。

### 4.6 Worker 侧

- **声明与 UI**：`worker/ui/index.html:163-166` 增加 `<input name="runtimes" type="checkbox" value="velites" /> Velites`；`app.js` 通用逻辑无需改。Worker 配置 `runtimes` 加 `velites` 后注册上送（host_client.py:70）。
- **二进制分发（Phase 2 定夺选型）**：
  - 方案 1（推荐起步）：维持手工 `cargo build --release` + PATH，安装/版本核对/与 Host 契约版本对齐写进 `docs/remote-execution-runbook.md`。零机制成本，与金丝雀期一致。
  - 方案 2：打进 worker 部署交付物（`deploy/compose.worker.yaml` 镜像），随 worker 版本化。worker 数量增长后再做。
  - 方案 3（否决）：经 bundle/artifact 由 Host 下发二进制——平台相关、完整性与供应链成本高，当前规模不值得。
- **启动预检（建议随 Phase 2）**：Worker 启动/注册时对 declared runtimes 所需二进制做 `shutil.which` 探测，缺失则拒绝声明该 runtime 并给出明确错误——避免"声明了 velites 但 PATH 没有"导致 claim 后 spawn 即失败、空转重试。

### 4.7 金丝雀结论承接与回退预案

金丝雀期回退语义是"改 `config/workflow.yaml` 一个字段 `flavor: pi` + 重启"（config/workflow.yaml:119 注释）。runtime 模型下的等价物：**把该 agent 定义的 `runtime: velites` 改回 `runtime: pi` + 重启**——粒度更细（单个 agent），操作同质（单字段 yaml 改动）。

事故预案（按爆炸半径）：

1. 单 agent 异常：该定义迁回 `runtime: pi`（配置回退，在途 claimed/running 执行不受影响）。注意 flavor 仍为 `velites` 时，迁回 pi 的 agent 依旧跑 velites 二进制——要回到 pi 二进制需同时落 `flavor: pi`。
2. velites 系统性异常：`flavor: pi` + 全部定义迁回 `runtime: pi`，与金丝雀期预案一致，一次配置变更完成。
3. 沙箱异常：`velites_no_sandbox: true` 逃生门继续有效——runtime=velites 的 manifest 同样携带该键（dispatch.py:86 → velites_command.py:107-108 追加 `--no-sandbox`）。
4. Worker 舰队能力回退：Host 侧吊销/降声明某 Worker 的 velites runtime 后，sweeper 的 runtime 维度（§4.3）保证后续 velites 请求不会 queue-rot，而是带明确原因失败。

### 4.8 invariant / 证据矩阵同步

- 既有四条（EXEC-EVENT-SCHEMA-001、EXEC-HARNESS-ISOLATION/BUDGET/SANDBOX-001）**不改**：它们约束 harness 本体，与 runtime 定位无关。
- 新增 invariant（registry 与证据矩阵各加一行）：
  - `EXEC-RUNTIME-DISPATCH-001`："Agent command construction is selected by `AgentDefinition.runtime`: `velites` pins the velites builder regardless of `workflows.pi.flavor`; `pi` delegates implementation choice to flavor; unknown runtimes fail fast at dispatch." Evidence：`tests/workflows/test_velites_command.py`（runtime 分派用例）、`tests/test_agent_broker.py`（dispatch fail-fast 用例），gate quick。
  - `EXEC-CLAIM-RUNTIME-001`（或与现有 claim invariant 合并）："A queued Agent request whose definition runtime is declared by no non-revoked Worker is failed by the unclaimable sweeper with an explicit runtime reason; it never rots in queued." Evidence：`tests/test_agent_broker.py` 新增 sweeper runtime 用例，gate quick。
- AGENTS.md §6 velites 条目在 Phase 2 更新为 runtime 模型表述。

## 5. 分阶段实施顺序

每阶段独立可交付、可回退（回退 = revert + 配置还原）。

- **Phase 1 — 枚举与链路放开**（纯代码，无生产行为变化）：
  1. §4.1 全部枚举点 + OpenAPI 重新生成；
  2. §4.2 dispatch 分派 + execution_catalog_projection 放宽；
  3. §4.3 sweeper runtime 维度；
  4. §6 测试；
  5. invariant registry 与证据矩阵加 §4.8 条目。
  交付：`runtime: velites` 定义在技术上可用，但生产 yaml 不迁任何 agent。回退：revert。
- **Phase 2 — per-agent 灰度**：
  1. Worker 舰队按 §4.6 声明 velites（UI checkbox、二进制到位、启动预检）；
  2. 按 §4.4 顺序逐个迁移 agent 定义，每步观察；
  3. AGENTS.md §6、`docs/remote-execution-runbook.md` 同步。
  交付：生产按 agent 粒度跑 velites runtime。回退：单定义迁回（§4.7）。
- **Phase 3 — flavor 语义收窄**（全量迁移完成后）：进入 §4.5 阶段 B，文档收口；flavor 删除（阶段 C）另立项。

## 6. 测试策略

- **契约/枚举**：`tests/test_agent_catalog.py` 增加 `runtime: velites` 合法化与非法值 fail-fast 用例；`tests/routes/test_agent_workers.py` 增加 runtimes 含 velites 的注册用例；export_openapi 后前端 typecheck 过（generated api.ts 派生纪律）。
- **命令构建**：`tests/workflows/test_velites_command.py` 增加 runtime 分派用例——runtime=velites 时 flavor=pi 也产出 velites argv；runtime=pi + flavor 两值各产出对应 argv；openclaw fail-fast。
- **claim 匹配**：`tests/test_agent_broker.py` 增加——仅声明 pi 的 Worker 不能 claim velites 请求；声明 velites 的可以；混合舰队各取所需。
- **sweeper**：`tests/test_agent_broker.py` 增加——无 Worker 声明 velites 时 velites 请求被 fail 且 error 含 runtime reason；零 Worker 时不动作的保护不变。
- **迁移/双 runtime 并存**：增加迁移场景测试——队列中同时存在 pinned 旧 hash（runtime=pi）与新 hash（runtime=velites）请求时，stale sweeper 处理旧的、claim 正常处理新的；`tests/full/test_velites_harness_e2e.py` 补一条经 Agent Worker 全链路的 runtime=velites e2e（如已有 flavor e2e 则复制改 runtime）。
- **回归**：`tests/executors/test_velites_event_contract.py` 等四条 invariant 证据测试保持绿（升格不改 harness）。
- 新测试按子系统放入对应子目录（`tests/workflows/`、`tests/` 现有 broker 文件就近），不新增 `tests/` 根目录文件（纯静态的加 `@pytest.mark.no_db`）。

## 7. Quality Impact

- 质量门：每阶段完成后 `./scripts/check-quick.sh`；交接前 GitHub Actions full gate（backend + frontend）通过。Phase 1 涉及 OpenAPI 再生成，前端 job 必须跑。
- 覆盖率：新增 dispatch 分派、sweeper 探测、UI checkbox 均需测试覆盖，维持 85% floor。
- 文件体积：dispatch.py（135 行）、unclaimable.py（138 行）改动量小，不触体积预算；若分派逻辑增长则按既有 `_lease_*`/`agent_broker/*` 拆分惯例抽模块。
- 配置纪律：不改 tracked yaml 的 secret 规则；`config/workflow.yaml` 的 agent 定义迁移属业务配置变更，走正常评审。
- 架构治理：新增 invariant 已按 §4.8 登记 registry + 证据矩阵；AGENTS.md §6 同步在 Phase 2 完成。
- 多 worktree：迁移类改动（definition_hash 变化）不与共享库混用，遵守 worktree 独立 Postgres 纪律。

## 8. 风险与开放问题

- **迁移窗口的 stale 失败**：改 runtime 使 pinned 旧 hash 请求 stale 失败（§4.4），需要 runbook 明确"低峰 + 排空 + 重提"流程；长期可考虑 definition 别名/双 hash 宽限，本期不做。
- **Worker 声明与实际二进制漂移**：声明了 velites 但机器上没有二进制 → spawn 失败。Phase 2 的启动预检缓解；分发工程化（§4.6 方案 2）是后续议题。
- **manifest `"pi"` 命名遗留**：runtime=velites 的 manifest 仍带 `"pi"` 块，语义别扭但无功能问题；改名与 command_spec version 升级捆绑在 Phase 3-C。
- **flavor 与 runtime 双轨认知成本**：过渡期内"为什么 flavor=velites 还要求迁 runtime"需靠本文档 + AGENTS.md 讲清楚，避免运维误配（两者语义见 §3）。
