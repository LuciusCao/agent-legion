# velites：Agent Legion 自研轻量 Agent Harness 设计

日期：2026-07-31 ｜ 状态：Draft（待评审）｜ 前置：[PoC 报告](./velites-poc-report.md)（worktree `poc-rust-pi`）

> velites（罗马军团轻步兵）是 Agent Legion 的专用 agent 执行内核：Rust 实现、单静态二进制、
> 极简上下文、可控性（controllability）作为第一特性。它替代 Node 版 Pi CLI 承担
> workflow 节点的 headless 执行。

## 1. 背景与动机

当前 worker 上每个节点执行 = 冷启动一个 Node Pi 进程。2026-07-31 实测（生产 64 并发形态）：

- 58 个并发 pi 进程总 RSS ≈ 5.3 GB（均值 93 MB，峰值单进程 630 MB）；
- 每周 ~5.3 万次节点执行，每次付 Node 启动 + 模块加载（实测 1.5–1.7 s CPU）；
- `--mode json` 的 `message_update` delta 占 stdout 体积 99%+，Pi 侧序列化、worker 泵逐字节
  扫描后全部丢弃——协议层面的纯浪费，且无法通过配置关闭（PoC 已确认）。

PoC（pi_agent_rust 替换验证）同时证明了两件事：

1. 收益空间是真实的：同负载 Rust 实现 RSS ≈ 1/6.5、启动 CPU ≈ 0；
2. 但我们对 harness 的需求面极窄（3 个工具 + skill 注入 + 事件流 + 一个 OpenAI 兼容
   provider），fork 一个 30 万行、以插件/TUI 为主体的移植版是背着债务起步。

因此决策：**自研 velites**，范围严格限定在 Agent Legion 的真实消费面内。

## 2. 目标与非目标

### 目标

- 单静态 Rust 二进制，冷启动 <50 ms，稳态 RSS 基线 <30 MB（上下文缓冲除外）；
- 完整复刻 Host 侧消费的事件流契约（见 §4），Host 三处消费方（日志渲染、token 计量、
  失败判定）零改动切换；
- skill 注入（显式 `--skill` 目录）+ `read`/`write`/`bash` 三工具；
- OpenAI 兼容 streaming chat（SSE）对接自建 LLM gateway；
- **可控性内建**（§5）：预算、优雅取消、输出自检、零自动发现；
- 单进程单执行，进程模型与现状一致（worker 隔离语义不变）。

### 非目标（明确不做）

- TUI / 交互模式 / 会话恢复；
- 插件、扩展、MCP、prompt template、主题（Pi 的这些我们本就全部 `--no-*` 关闭）；
- 多 provider 原生适配（只认 OpenAI 兼容 endpoint，其余由 gateway 收敛）；
- 通用开源 harness 产品化（velites 是 Agent Legion 的专用配件；schema 稳定后再评估）。

## 3. 仓库组织与总体架构

### 组织：monorepo 内 crate

```
velites/                 # Cargo crate（本仓库根下新目录）
  Cargo.toml
  src/
    main.rs              # CLI 入口（clap）
    lib.rs               # 库入口（供将来嵌入/契约测试复用）
    cli.rs               # 参数定义
    agent.rs             # agent loop
    events.rs            # 事件 schema 定义（serde）+ emitter
    tools/{read,write,bash}.rs
    provider/{client,openai_compat}.rs
    skill.rs             # SKILL.md 加载
    budget.rs            # 预算治理
    cancel.rs            # 取消/信号
    sandbox.rs           # 沙箱抽象（seatbelt / bubblewrap，§5）
  tests/                 # Rust 集成测试（含 golden event fixtures）
```

理由：事件 schema 与 Host 消费方强耦合，monorepo 内可做到"schema 变更 + 消费方适配 +
契约测试"单 commit 闭环；CI 复用现有 lane 裁剪（纯 velites 改动不跑 backend pytest）。
拆独立的触发条件：有外部用户，或 schema 连续半年无变更。

依赖纪律：主流基础库（tokio、reqwest、serde/serde_json、clap、schemars），不引入
agent 框架；依赖清单评审纳入 PR。

### 运行形态

worker 侧进程模型不变：`worker/executor.py` 每个 claim 起一个 velites 子进程
（`subprocess.Popen(cwd=job_dir, start_new_session=True)`），stdout 即事件流。
二进制经 Dockerfile 新增 rust build stage 打进 worker 镜像；`config/workflow.yaml`
的 `workflows.pi.binary` 指向切换（保留回退 Node Pi 的能力一个版本周期）。

## 4. 事件 Schema v1：pi 兼容子集（velites/json1）

**策略：wire 兼容 Pi 被消费的事件子集，砍掉 delta 类事件，字段只增不改。**

**事件流是 velites 的核心交付物，不可裁剪**：stdout 事件流经 worker/runner 原样
落盘为 run 目录的 `events.jsonl`，它同时是——UI 过程预览的数据源
（`job_logs.py` 读 `events.jsonl` 渲染 agent 的思考 text/thinking 与工具调用过程）、
token 计量依据、失败判定依据。砍 delta 不影响预览：预览渲染的是语义边界事件
（message/turn/tool 的起止），从不渲染 `message_update`。

### 必发事件（与 Host 消费方一一对应）

| 事件 | 消费方 | 关键字段 |
|---|---|---|
| `session` | `pi_event_scan.py` allowlist / 日志渲染 | `sessionId`（可用 `--name` 等价标识） |
| `agent_start` / `agent_end` | 日志渲染 | `messages`、`error`；M3 起 `agent_end` 增加可选 `reason`（`budget_exceeded` / `cancelled`，正常结束与模型错误时缺省） |
| `turn_start` / `turn_end` | 日志渲染 | `turnIndex`、`message`、`toolResults` |
| `message_start` | 日志渲染 | `message` |
| `message_end` | **token 计量 + 失败判定** | `message.usage.{input,output,cacheRead}`、`provider`、`model`、`stopReason`、`content[]`（`text`/`thinking`/`toolCall`）、`errorMessage` |
| `auto_retry_start` | 重试可观测性（pi 兼容，无渲染） | `attempt`（1 起）、`maxAttempts`、`delayMs`、`error` |
| `tool_execution_start` / `tool_execution_end` | 日志渲染 | `toolCallId`、`toolName`、`args`、`result.content`、`isError` |
| `outputs_validation` | 输出自检结果（velites 扩展，无渲染） | `missing`（字符串数组）；只要给了 `--require-output` 且运行正常结束/预算耗尽结束就**总是**发出（含 `missing: []`），便于 Host 明确判定；取消或未恢复的模型错误路径不发 |

### 明确不发

- `message_update` / `tool_execution_update`（delta 类）——协议层删除，worker
  `event_filter.py` 的 delta 快路径随之成为死代码（后续清理，不在本期）。

### 错误语义（与 `pi_model_error.py` 对齐）

- 模型调用失败先内部重试（指数退避，上限可配）；每个失败的 transient attempt 在
  退避 sleep 前发出一对 pi 兼容事件：assistant `message_end`（`stopReason=error` +
  `errorMessage`，usage 为 0）+ `auto_retry_start`；恢复后续跑，最终 assistant message
  `stopReason` 为 `stop`/`toolUse`——Host 据此清除瞬时错误（pi 无 `auto_retry_end`，
  velites 同样不发）；
- 未恢复：最后一条 assistant message 带 `stopReason=error` + `errorMessage`，
  **exit 0**（复刻 Pi"模型 400 也 exit 0"，Host 靠事件流判失败）；
- 取消（SIGTERM 优雅收尾，§5）：`agent_end{reason: "cancelled"}`，**exit 0**——
  取消是 Host 主动行为而非 harness 故障（M3 已决）；
- exit ≠ 0 仅用于 harness 自身故障（参数错误、内部 panic、被信号硬终止）。

### Schema 治理

- 事件结构在 `velites/src/events.rs` 用 serde 定义，`schemars` 导出 JSON Schema；
- Python 侧新增契约测试：校验 Host 消费字段在 schema 中存在且类型一致
  （quick lane 必跑）；
- full lane 增加**真二进制集成测试**（偿还当前 `tests/executors/` 全 mock 的债）：
  用 fixture provider（本地 stub SSE server）跑完整执行，断言事件序列与 run.json；
- PoC 的 `poc/diff_events.py` 思路固化为 golden diff 测试（Node Pi 输出作参照，仅开发期）。

## 5. 可控性特性（第一特性）

核心五项（评审已确认）：

| 特性 | 设计 | 现状对照 |
|---|---|---|
| 预算内建 | `--max-turns`、`--max-tokens`（按 usage 累计）、wall-clock deadline（复用 `--timeout-seconds`，M3 起该 flag 同时界定整个 run 的墙钟上限与单次 provider HTTP 超时）；每次模型调用**前**检查，耗尽时注入一条收尾消息给模型**一个**收尾轮写出已声明产物，然后结束，`agent_end{reason: "budget_exceeded"}`（M3 实现为可选 `reason` 字段，取 `budget_exceeded` / `cancelled` 两值，替代布尔 flag 方案）；预算值走 `AgentDefinition.config_schema` 解析链成为节点标准可调参数 | 现在只有外层 wall-clock 强杀 |
| 优雅取消 | SIGTERM 被 loop 捕获：检查点在 turn 边界与每次工具执行完成后（模型调用也可被中断）；bash 工具正在跑时走既有 TERM→grace→KILL 进程组清理；收尾发出 `agent_end{reason:"cancelled"}` 再退出，**exit 0**（取消是 Host 主动行为，非 harness 故障；M3 已决）；SIGKILL 兜底语义不变 | 现在 cancel = 进程组强杀，无事件收尾 |
| 输出自检 | `--require-output <file>`（可多次）；loop 正常结束前自检缺失项（路径走工具同款 cwd 沙箱校验，逃逸路径启动即报错），有缺失则注入系统消息给**一次**补救轮；最终**总是**发 `outputs_validation{missing:[...]}` 事件（`missing` 可为空，M3 已决：显式事件便于 Host 判定） | 现在 Host 事后扫 job_dir 才发现缺失 |
| 零自动发现 | 不读 AGENTS.md/CLAUDE.md、不扫描 skill/扩展/模板目录、不读用户级配置；上下文 = `--system-prompt` + `--skill` + `@prompt.md`，无第三个来源（代码层不存在发现逻辑，而非"有逻辑加开关"） | Pi 靠 4 个 `--no-*` flag 维持；pi_agent_rust 无开关（PoC 的 P0 阻断项） |
| 无 delta | 见 §4 | 现在 99% 的 stdout 体积是被丢弃的 delta |

评审后调整的两项：

- **上下文体积护栏 → 移至 §12 开放问题**：先做度量（工具层只记录输出体积指标，
  不截断），M2 用真实负载分布决定是否设阈值；
- **密钥管理 → 降级为普通配置项**（见 §7），不作为治理特性。

### 沙箱：文件系统边界 OS 级强制（M4.5，已拍板）

背景：2026-07-31 晚生产事故——一个提示词 bug 导致 pi 递归扫描整个代码仓库
（find/grep 风暴），CPU 打满、当批节点失败率 40%。pi 没有任何沙箱，agent 的文件
操作边界只剩提示词自觉。velites 把文件系统边界做成操作系统级强制，提示词失误
无法突破。

范围（本期只做文件系统）：

- **读**允许：cwd（job 目录）+ session dir + 显式 `--skill` 目录（只读）+
  系统库/二进制（只读，进程执行必需）；
- **写**允许：仅 cwd + session dir + `/tmp`（含 `$TMPDIR`）；
- **网络本轮不限制**（模型调用必须出网，且出口收敛于 gateway；网络策略另行立项）；
- **默认开启**，`--no-sandbox` 作为运维逃生门（worker 正常路径不传）。

实现：

- `Sandbox` 抽象（`velites/src/sandbox.rs`），按平台二选一：macOS 用
  `sandbox-exec`（运行时生成 seatbelt profile），Linux 用 `bubblewrap`
  （worker 镜像需验证 user namespace 可用性）；
- 生效点：**bash 工具的子进程整体用沙箱包装**（OS 级强制，子进程再 fork 也受限）；
  read/write 工具维持 §8 的 canonicalize 进程内路径沙箱——两层互补：进程内校验
  防 read/write 逃逸，seatbelt/bwrap 防 bash 及其子孙进程逃逸；
- **fail-closed**：沙箱不可用（seatbelt/bwrap 缺失、profile 生成失败、userns 被禁）
  时启动即 exit≠0 报错，**不降级**为无沙箱运行（逃生门只有显式 `--no-sandbox`）；
- 沙箱拒绝表现为工具失败：`tool_execution_end{isError: true}`，错误消息含
  sandbox 拒绝信息，随 events.jsonl 落盘可审计。

Linux 运行时要求（CI run 30683781370 实测）：bwrap 需要 setuid 位或非特权
user namespace 之一；Ubuntu 24.04 默认通过 AppArmor 限制非特权 userns
（`bwrap: setting up uid map: Permission denied`），CI 与 worker 镜像统一
采用 setuid 方案（`chmod u+s /usr/bin/bwrap`）。另外默认 Docker seccomp
profile 拦截 `unshare`，容器部署需放行（compose 侧配置，M5 灰度前在真
worker 上验证）。

macOS seatbelt 的两处实现注记（实测 macOS 15，`deny default` 下 dyld/libsystem
在 exec 时直接 abort/bus error，profile 必须放宽这两处，进程才起得来）：
`file-read-metadata` 全局放行（仅 stat，读不到文件内容与目录项），
`file-read-data` 附加 `(literal "/")`（根目录在启动时被打开）。内容边界不受影响：
允许根之外的 `open(O_RDONLY)` 与 `readdir` 仍被 EPERM 拒绝。

## 6. CLI 接口

```
velites --mode json \
        --session-dir <run>/session \
        --skill <dir> \
        --tools read,write,bash \
        --provider gateway --model <m> --thinking low \
        [--max-turns N] [--max-tokens N] [--require-output f ...] \
        [--no-sandbox] \
        @<run>/prompt.md "Execute the attached node instructions."
```

- provider `gateway`：base URL 与 key 来自 velites 配置文件（见 §7 凭据配置）；
- `--mode` 只有 `json`（headless 唯一形态）；
- 未知 flag 直接报错退出（与 Pi/pi_agent_rust 的静默吞掉相反，防止配置漂移）；
- `--name` 保留（仅标识用途，写入 `session` 事件）。

## 7. Provider 层

- 仅实现 OpenAI chat completions（SSE streaming）；请求/重试/usage 解析一处收敛；
- `thinking` 参数按 provider 映射（初期只支持 gateway 现有映射，PoC 已验证）；
- 已知边界：严格要求 SSE——gateway 上只回 `application/json` 的模型不可用
  （PoC P2 发现），模型白名单在 `config/workflow.yaml` 侧约束并写进运维文档；
- HTTP 超时可配（`--timeout-seconds`，默认 600 s，对齐 gateway 长生成场景；M3 起
  该值同时是整个 run 的 wall-clock 预算，见 §5 预算内建），`retry{max_retries, backoff}` 可配。

### 凭据配置（简化版，评审确认）

- 初期：velites 自有配置文件（`~/.velites/config.json`，含 gateway `base_url` +
  `api_key`），文件权限 0600。**明文文件是权宜之计，不作为长期方案**；
- 后续扩展：env 注入（`--api-key-env`）→ 与 Agent Legion vault 打通，按优先级
  env > 文件覆盖；
- 保留的底线仅一条：secret 不上命令行（`ps` 可见）。不做启动强校验等其他治理。

## 8. 工具实现

- **read**：路径必须解析在 cwd 内（拒绝逃逸），支持行区间读取；
- **write**：tmp + rename 原子写，同 cwd 沙箱；
- **bash**：`cwd=job_dir`，env 继承父进程；超时 → 进程组 TERM → grace → KILL（对齐
  Pi 语义，Rust 下用 `process-group` 或手动 `killpg`）；
  必须能跑 `python3`（skill scripts 依赖，worker 镜像已具备）；
- **体积度量（不设截断）**：所有工具的输入/输出体积记入 `tool_execution_end`
  （如 `output_bytes`、`truncated: false` 预留字段），为 §12 的护栏决策积累
  真实分布数据。

## 9. 与 Agent Legion 的集成与切换

1. `server/app/workflows/pi_protocol.py`：PoC 已验证的 flavor 开关
   （`AGENT_LEGION_PI_FLAVOR`）转正为配置项 `workflows.pi.flavor: pi|velites`；
2. worker bundle：二进制不打进 bundle（bundle 只带 skill + prompt），由 worker 镜像
   或 worker 侧配置提供路径；
3. 灰度路径：
   - Phase 0：契约测试 + 真二进制集成测试入库（不启用）；
   - Phase 1：shadow 对比——同一节点双跑（velites 结果不采用），diff 事件流与产出；
   - Phase 2：单 capability 金丝雀（建议 `review_subtitles`，PoC 已验证）；
   - Phase 3：默认切换，Node Pi 保留一个版本周期后移除 flavor；
4. 回退：`workflows.pi.flavor: pi` 即回退，无数据迁移。

## 10. Quality Impact

- **新 invariant 候选**（进 `config/architecture/architecture-invariants.yaml`）：
  - `EXEC-EVENT-SCHEMA-001`：velites 事件 schema 与 Host 消费字段契约一致
    （quick：schema 导出 + Python 契约测试）；
  - `EXEC-HARNESS-ISOLATION-001`：harness 无自动发现——上下文来源仅
    system-prompt/skill/prompt 三者（quick：含 AGENTS.md 的 fixture 目录执行，
    断言首条 message 不含其内容）；
  - `EXEC-HARNESS-BUDGET-001`：预算耗尽必以 `agent_end{budget_exceeded}` 收尾
    （quick：stub provider 集成测试）；
  - `EXEC-HARNESS-SANDBOX-001`：默认沙箱下 bash 工具无法读写
    cwd/session dir/`/tmp` 之外的文件（quick：沙箱集成测试——尝试读用户 home
    敏感路径、写仓库外路径，断言 `tool_execution_end{isError: true}`）；
    沙箱不可用时 fail-closed（exit≠0），不降级；
- **测试债偿还**：`tests/executors/` 目前对 pi 全部 fake-binary mock；本期为 velites
  建立真二进制 + stub provider 的集成测试（full lane），pi flavor 维持 mock 至移除；
- **压力门禁**：并发 RSS/启动延迟基准纳入 stress lane（对照 PoC 基线：单发 RSS
  <30 MB、冷启动 <50 ms）；
- **体积预算**：velites 为 Rust crate，不进 Python 体积预算；CI 新增 rust lane
  （`cargo fmt --check`、`clippy -D warnings`、`cargo test`），按路径裁剪；
- **安全**：secret 不上命令行（`ps` 可见）这一条保留；凭据初期走 0600 配置文件，
  env/vault 为后续扩展（§7）；VAULT-SECRET-001 边界不变化——harness 不接触 vault；
  文件系统沙箱默认开启（§5 沙箱小节，EXEC-HARNESS-SANDBOX-001），回应 2026-07-31
  pi 扫全仓库事故；
- **文档**：README 增 velites 章节；本文件进 `docs/architecture/`；
  AGENTS.md 第 6 节 executor 扩展链补 velites 边界说明。

## 11. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 骨架 | crate 初始化、CLI、事件 emitter、stub provider 的 agent loop（read/write/bash） | cargo test 绿；stub 下 golden 事件序列 |
| M2 契约对齐 | OpenAI 兼容 SSE provider、skill 加载、错误/重试语义 | 真 gateway 跑 `review_subtitles` fixture，与 Node Pi diff 通过 |
| M3 可控性 | 预算/取消/输出自检 + 工具体积度量 | 三条 invariant 测试入库 |
| M4 集成 | flavor 配置、Dockerfile rust stage、CI rust lane、集成测试 full lane | `./scripts/check-quick.sh` + full gate 绿 |
| M4.5 沙箱 | §5 沙箱小节：Sandbox 抽象、macOS seatbelt 先行、Linux bwrap 在 worker 镜像验证、`EXEC-HARNESS-SANDBOX-001` | 沙箱集成测试入库（quick lane）；逃逸尝试全部被拒 |
| M5 灰度 | shadow → 金丝雀 → 默认 | 生产 64 并发下 RSS/CPU 对比报告 |

## 12. 风险与开放问题

- **上下文体积护栏（评审保留项，度量先行）**：疑虑是截断可能伤 agent 表现、且阈值
  缺乏依据（PoC 观测到 630 MB RSS 峰值与上下文膨胀相关，但样本有限）。决策路径：
  M2 起工具层只度量不截断（§8），用真实负载的输出体积分布决定是否设阈值、
  阈值多少、截断提示语怎么写；
- **SSE 方言**：gateway 背后不同模型的 SSE 细节差异（PoC P2）——M2 用真实模型矩阵
  验证；缓解：解析器对非标准 event 行容错跳过；
- **thinking 参数映射**：不同后端 wire 参数不同——初期只支持 gateway 当前映射，
  新后端接入时显式扩展；
- **prompt 兼容性**：SKILL.md 中的指令对模型行为的引导经 Pi 验证过，velites 的
  system prompt 拼装顺序不同可能改变行为——M2 diff 不仅比事件结构，也抽查产出质量；
- **工作量估计**：M1–M3 约 1.5–2 周（loop 本身小，成本在工具鲁棒性与 provider 兼容），
  M4–M5 约 1 周。

### 已决项

- **`--session-dir`（评审已决）**：保留 flag（CLI 兼容），velites 落一个自有格式的
  `session.jsonl`（消息历史的镜像落盘，成本≈0），但**不提供 resume 入口**。
  注意区分：`events.jsonl`（stdout 归档）是 UI 过程预览/token 计量/失败判定的
  数据源，必须完整；`session.jsonl` 只是为将来"中断恢复（resume）"特性预留的
  素材。resume 作为可控性家族的候选第八特性，待 velites 切换稳定后单独立项
  （涉及 session 格式 v1、Host 重跑链路、上下文信任边界，需独立设计）。
