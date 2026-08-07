# velites：Agent Legion 自研轻量 Agent Harness 设计

日期：2026-07-31 ｜ 状态：已落地（现行设计文档；2026-08-03 升格落地、2026-08-04 金丝雀关闭，见 §9）｜ 前置：[PoC 报告](./velites-poc-report.md)（worktree `poc-rust-pi`）

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
    config.rs            # gateway 凭据（文件 + env 覆盖，§7）
    session.rs           # session.jsonl 镜像落盘（--session-dir）
    tools/{mod,read,write,bash,truncate}.rs
    provider/{mod,openai_compat,retry,stub}.rs
    skill.rs             # SKILL.md 加载
    budget.rs            # 预算治理
    cancel.rs            # 取消/信号
    sandbox.rs           # 沙箱抽象（seatbelt / bubblewrap，§5）
    bin/velites_schema.rs # 事件流 JSON Schema 导出（schemars）
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
二进制经 Dockerfile 新增 rust build stage 打进 worker 镜像；命令构建由
`AgentDefinition.runtime` 钉死分派（`server/app/agent_broker/dispatch.py`，
EXEC-RUNTIME-DISPATCH-001）——pi → pi argv、velites → velites argv、
openclaw 未实现即 fail-fast；pi 不退役、长期保留，
灰度/回退均为单 agent 定义的单字段配置改动（详见 §9）。

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
| `session` | `shared/pi_events.py` allowlist / 日志渲染 | `sessionId`（可用 `--name` 等价标识） |
| `agent_start` / `agent_end` | 日志渲染 | `error`；M3 起 `agent_end` 增加可选 `reason`（`budget_exceeded` / `cancelled`，正常结束与模型错误时缺省）。schema v2 起 `agent_end` 不再携带 `messages` 全量历史（无 Host 消费方，消息内容已由各 `message_end` / `tool_execution_end` 承载） |
| `turn_start` / `turn_end` | 日志渲染 | `turnIndex`。schema v2 起 `turn_end` 不再携带 `message` / `toolResults` 冗余拷贝（同回合 `message_end` 与 `tool_execution_end.result.content` 已是唯一内容载体） |
| `message_start` | 日志渲染 | `message` |
| `message_end` | **token 计量 + 失败判定** | `message.usage.{input,output,cacheRead}`、`provider`、`model`、`stopReason`、`content[]`（`text`/`thinking`/`toolCall`）、`errorMessage`、可选 `timing`（见下） |
| `auto_retry_start` | 重试可观测性（pi 兼容，无渲染） | `attempt`（1 起）、`maxAttempts`、`delayMs`、`error` |
| `tool_execution_start` / `tool_execution_end` | 日志渲染 | `toolCallId`、`toolName`、`args`、`result.content`、`isError` |
| `outputs_validation` | 输出自检结果（velites 扩展，无渲染） | `missing`（字符串数组）；只要给了 `--require-output` 且运行正常结束/预算耗尽结束就**总是**发出（含 `missing: []`），便于 Host 明确判定；取消或未恢复的模型错误路径不发；收尾仍缺失的非取消运行 exit 1（输出契约违约，EXEC-HARNESS-OUTPUTS-001） |

### 请求级计时（velites 扩展，Pi 无对应能力）

assistant `message` 上的可选 `timing` 字段（`src/events.rs` 的 `RequestTiming`），
由真实 provider（`openai_compat`）在**成功**完成的请求上填充，stub provider 与
错误 `message_end`（transient 失败 / 未恢复）一律缺省（`None`，wire 上整个键不出现）。
三个子字段均为 wall-clock 毫秒整数：

| 字段 | 语义 |
|---|---|
| `ttfbMs` | POST 发出 → 首个 SSE `data:` chunk（非流式 JSON 回退方言则为 → 响应头） |
| `streamMs` | 首个 → 末个 SSE `data:` chunk（`[DONE]` 或连接结束） |
| `totalMs` | POST 发出 → 流结束 |

不变式：`ttfbMs ≤ totalMs`、`streamMs ≤ totalMs`。重试场景每次 attempt 独立计时，
最终只挂在成功那次的 assistant message 上（`auto_retry_start` 失败对无时间字段）。
TPS 不冗余存储：消费方按 `usage.output / (streamMs / 1000)` 自行计算。
该字段为 additive 扩展，Host 现有消费方（`token_usage` / `shared/pi_events` /
`job_log_renderer`）均为 `dict.get` 风格，对未知字段天然容忍。

### 明确不发

- `message_update` / `tool_execution_update`（delta 类）——协议层删除，worker
  `event_filter.py` 的 delta 快路径随之成为死代码（后续清理，不在本期）。

### 错误语义（与 `shared/pi_model_error.py` 对齐）

- 模型调用失败先内部重试（指数退避，上限可配）；每个失败的 transient attempt 在
  退避 sleep 前发出一对 pi 兼容事件：assistant `message_end`（`stopReason=error` +
  `errorMessage`，usage 为 0）+ `auto_retry_start`；恢复后续跑，最终 assistant message
  `stopReason` 为 `stop`/`toolUse`——Host 据此清除瞬时错误（pi 无 `auto_retry_end`，
  velites 同样不发）；
- 未恢复：最后一条 assistant message 带 `stopReason=error` + `errorMessage`，
  **exit 0**（复刻 Pi"模型 400 也 exit 0"，Host 靠事件流判失败）——但声明了
  `--require-output` 且收尾仍缺失时按下条输出契约违约处理；
- 取消（SIGTERM 优雅收尾，§5）：`agent_end{reason: "cancelled"}`，**exit 0**——
  取消是 Host 主动行为而非 harness 故障（M3 已决）；
- 输出契约违约（EXEC-HARNESS-OUTPUTS-001）：`--require-output` 声明件在非取消
  运行收尾时仍缺失 → **exit 1**，不给调用方留"exit 0 假完成"的口子；
- 其余 exit ≠ 0 仅用于 harness 自身故障（参数错误、缺网关凭据、内部 panic、
  被信号硬终止；exit 2）。

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
| 预算内建 | `--max-turns`、`--max-tokens`（按 usage 累计）、wall-clock deadline（复用 `--timeout-seconds`，M3 起该 flag 界定整个 run 的墙钟上限；**不再**兼任单次 provider HTTP 总超时——总超时会掐断长生成流，HTTP 层改为 connect 超时 + chunk 间 idle 超时，见 §7）；每次模型调用**前**检查，耗尽时注入一条收尾消息给模型**一个**收尾轮写出已声明产物，然后结束，`agent_end{reason: "budget_exceeded"}`（M3 实现为可选 `reason` 字段，取 `budget_exceeded` / `cancelled` 两值，替代布尔 flag 方案）；预算值走 `AgentDefinition.config_schema` 解析链成为节点标准可调参数 | 现在只有外层 wall-clock 强杀 |
| 优雅取消 | SIGTERM 被 loop 捕获：检查点在 turn 边界与每次工具执行完成后（模型调用也可被中断）；bash 工具正在跑时走既有 TERM→grace→KILL 进程组清理；收尾发出 `agent_end{reason:"cancelled"}` 再退出，**exit 0**（取消是 Host 主动行为，非 harness 故障；M3 已决）；SIGKILL 兜底语义不变 | 现在 cancel = 进程组强杀，无事件收尾 |
| 输出自检 | `--require-output <file>`（可多次）；loop 正常结束前自检缺失项（路径走工具同款 cwd 沙箱校验，逃逸路径启动即报错），有缺失则注入系统消息给**一次**补救轮；最终**总是**发 `outputs_validation{missing:[...]}` 事件（`missing` 可为空，M3 已决：显式事件便于 Host 判定）；补救后仍缺失的非取消运行 **exit 1**（EXEC-HARNESS-OUTPUTS-001） | 现在 Host 事后扫 job_dir 才发现缺失 |
| 零自动发现 | 不读 AGENTS.md/CLAUDE.md、不扫描 skill/扩展/模板目录、不读用户级配置；上下文 = `--system-prompt` + `--skill` + `@prompt.md`，无第三个来源（代码层不存在发现逻辑，而非"有逻辑加开关"） | Pi 靠 4 个 `--no-*` flag 维持；pi_agent_rust 无开关（PoC 的 P0 阻断项） |
| 无 delta | 见 §4 | 现在 99% 的 stdout 体积是被丢弃的 delta |

评审后调整的两项：

- **上下文体积护栏 → 移至 §12 开放问题**：先做度量（工具层只记录输出体积指标，
  不截断），用真实负载分布决定是否设阈值（2026-08-01 已拍板：按 pi 对齐的
  2000 行 / 50KB 双阈值截断，见 §8 与 §12）；
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

python3 读白名单（2026-08-02 金丝雀事故修复）：uv/Homebrew 系解释器的
libpython 经 `@rpath` 从安装前缀加载，前缀不在系统读白名单内时沙箱内
`python3` 直接 dyld 失败。启动时按 which 语义探测 PATH 上的 `python3`
（纯 PATH 搜索，不起子进程），把两层根以**只读**加入 seatbelt 白名单：
canonicalize 穿透 `.venv` symlink 得到的安装前缀（`bin` 的父目录）；以及
当 PATH 条目是 venv（`<venv>/bin/python3` 且存在 `<venv>/pyvenv.cfg`）时的
venv 根——CPython 的 site.py 先 stat（元数据全局放行）再 open `pyvenv.cfg`，
不在白名单内会在解释器启动时直接 EPERM 致命失败（B 面验证实测），放行
venv 根同时保证其 site-packages 可导入。防呆：安装前缀必须真实存在且
路径含 `python`，否则跳过（避免 `/usr/bin/python3` 误把 `/usr` 加进白名单
——系统路径本就覆盖）；探测失败静默跳过。Linux 的 `--ro-bind / /`
天然覆盖所有解释器位置，无需等价逻辑。

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

### `velites sandbox wrap`（EXEC-CODE-003）

```
velites sandbox wrap --cwd <dir> [--allow-read <dir> ...] [--allow-write <dir> ...] \
                     [--allow-network] -- <cmd...>
```

把单个命令包进 OS 沙箱执行（供 Host 的自定义 code 节点使用，设计
`custom-workflow-nodes-design.md` §7 二期）：与 bash 工具同一套策略生成
（seatbelt profile / bwrap argv，单一事实源）——默认 `deny`，`--cwd` 与系统
tmp 可写、`--allow-read` 只读、网络默认拒绝（macOS 无 network 规则、Linux
`--unshare-net`），`--allow-network` 按 capability 放开。fail-closed：后端
探测失败即非零退出，绝不裸跑。

部署注意：启用自定义节点（`workflows.custom_nodes_enabled`）后，**Host 机器**的
PATH 必须提供 velites 二进制（此前仅 Worker 侧要求）；缺失时自定义节点执行
fail-closed 报错，内置节点不受影响。

## 7. Provider 层

- 仅实现 OpenAI chat completions（SSE streaming）；请求/重试/usage 解析一处收敛；
- usage 口径对齐 pi：`input = prompt_tokens − cacheRead`（provider 的 `prompt_tokens`
  **含**缓存命中部分，pi 的 `input` 不含；缓存部分只经 `cacheRead` 单列计费，直接透传
  `prompt_tokens` 会把缓存部分双重计费——2026-08-01 生产数据核对：pi input 27.5k +
  cache 420k = velites 修复前 input 447k），`saturating_sub` 兜底异常网关；
- `thinking` 参数按 provider 映射（初期只支持 gateway 现有映射，PoC 已验证）；
- 已知边界：严格要求 SSE——gateway 上只回 `application/json` 的模型不可用
  （PoC P2 发现），模型白名单在 `config/workflow.yaml` 侧约束并写进运维文档；
- HTTP 层**不设整请求总超时**（总超时会掐断几分钟的长生成流；2026-08-01
  回放事故证实 gateway 会在上游失败时直接掐流）：内建 connect 超时 10s +
  chunk 间读 idle 超时 180s（思考模型的 chunk 间隔可能较长；run 级墙钟上限由
  `--timeout-seconds` 预算兜底，见 §5 预算内建），`retry{max_retries, backoff}` 可配。
  SSE 健壮性：传输错误沿 source chain 暴露根因（reqwest 顶层 Display 会抹平为
  "error decoding response body"）；`data:` 载荷 JSON 解析失败按瞬时流损坏处理
  （transient，可重试），不作确定性失败；行拼装按字节缓冲，跨 chunk 的多字节
  UTF-8 不会被切坏。
- 网关兼容性（2026-08-01 回放事故定位）：无 tool_calls 的 assistant 消息**禁止**
  发 `content: null`——gateway 收到后先回 200 再立即掐断连接（0 字节），重试
  无果；thinking-only 消息（thinking 不回传）序列化降级为 `""`。

### 凭据配置（简化版，评审确认）

- 初期：velites 自有配置文件（`~/.velites/config.json`，含 gateway `base_url` +
  `api_key`），文件权限 0600。**明文文件是权宜之计，不作为长期方案**；
- env 注入（已实现）：固定环境变量 `VELITES_BASE_URL` / `VELITES_API_KEY`
  （`velites/src/config.rs`）按字段覆盖文件值，env 全量提供时可完全免去配置
  文件；无 `--api-key-env` flag；
- 后续扩展：与 Agent Legion vault 打通（env 覆盖即预留的接入缝），优先级
  env > 文件；
- 保留的底线仅一条：secret 不上命令行（`ps` 可见）。不做启动强校验等其他治理。

## 8. 工具实现

- **read**：路径必须解析在 cwd 或任一 `--skill` 目录 / session dir 内（后两者为只读
  根，与 §5 OS 沙箱的读放行口径一致；`..`/symlink 逃逸一律拒绝），支持行区间读取；
- **write**：tmp + rename 原子写，仅限 cwd 沙箱（skill/session 目录绝不可写）；
- **bash**：`cwd=job_dir`，env 继承父进程；超时 → 进程组 TERM → grace → KILL（对齐
  Pi 语义，Rust 下用 `process-group` 或手动 `killpg`）；
  必须能跑 `python3`（skill scripts 依赖，worker 镜像已具备）；
- **输出截断（pi 对齐）**：工具输出按双阈值截断——2000 行 或 50KB
  （50×1024 字节），任一先到即截，语义与 pi `truncate.js` 完全一致
  （实现集中在 `velites/src/tools/truncate.rs`）：
  - 截断不切断行（不产生半行）；唯一例外是 bash 末行单行超限的边缘
    情况——保留该行尾部 50KB（UTF-8 字符边界对齐），并附
    `[Showing last <size> of line N (line is <size>). Full output: <path>]`；
  - **read → 截头保留**（truncateHead）：截断后追加
    `[Showing lines X-Y of N. Use offset=Z to continue.]`（byte 触发时附
    `(50KB limit)`）；首行单行超 50KB 时不输出内容，改为提示 bash 兜底
    `sed -n 'Np' <path> | head -c 51200`；用户显式传 `limit` 时先按
    limit 截取再应用截断，若文件还有剩余则提示
    `[R more lines in file. Use offset=Z to continue.]`；
  - **bash → 截尾保留**（truncateTail，错误/结果在尾部）：截断时把完整
    输出写到系统临时目录的 `velites-bash-*` 文件，追加
    `[Showing lines A-B of N. Full output: <path>]`（byte 触发附
    `(50KB limit)`）；
  - 工具 description 向模型声明截断规则（措辞对齐 pi）；
  - `output_bytes` 度量保留，语义为截断前体积。

## 9. 与 Agent Legion 的集成与切换

**当前模型（2026-08-05 起，agent 配置治理 phase 3 落地）**：pi、openclaw、
velites 是平级 runtime，由 `AgentDefinition.runtime` 声明（定义存
`versioned_entities` 表，Studio「Agent 管理」维护；yaml `agents:` 段与
`workflows.pi` 块已退役，出现在 yaml 中启动即报错）。命令构建按 runtime
钉死分派（EXEC-RUNTIME-DISPATCH-001）：`runtime: velites` → velites argv、
`runtime: pi` → pi argv；openclaw 未实现，dispatch fail-fast。执行配置
（provider/model/thinking）按严格链解析：节点 `execution.*` 覆盖 →
workspace `default_agent_*` → 报错，无全局兜底。manifest 的执行块统一为
`execution.*`（`binary/provider/model/thinking/timeout_seconds/no_sandbox`），
不再有 `pi.*` 键。灰度/回退粒度是单个 agent 定义的单字段改动，操作手册见
`docs/remote-execution-runbook.md` §6。

**flavor 的退役（2026-08-05）**：`workflows.pi.flavor` 实现选择层已随 yaml
块一并删除。此前保持 `runtime: pi` 的 4 个 video_knowledge agent 已由
schema v27 migration 翻转为 `runtime: velites`（新发 published 版本、归档
旧版）。`PiRuntimeConfig` 只剩硬编码默认（flavor="pi"），专供保留的本地
pi executor 死路径（`executors/pi.py` + PiRunner）。

**pi 的定位（2026-08-04 用户决策）**：pi **不退役**，作为可选 runtime 长期
保留——velites 是生产主力，pi 作为备选实现与对照基线继续可用
（`runtime: pi` 即完整 pi 路径）。若未来仅出于卫生目的清理
（如 command_spec version 升级），另行立项评估，与退役无关。

**回退**：单 agent 异常把该定义迁回 `runtime: pi`（Studio 改一个字段即
完成）；系统性异常将全部定义迁回 `runtime: pi`。沙箱异常当前需发版调整
（`velites_no_sandbox` 配置项已随 `workflows.pi` 退役；`execution.no_sandbox`
在 manifest 恒为 false）。

**历史灰度路径（已完成，存档）**：

- Phase 0：契约测试 + 真二进制集成测试入库（M4）；
- Phase 1 shadow = 抽样回放（`scripts/velites_replay.py` 离线双跑 pi 与
  velites，diff 事件流与产出）；
- Phase 2 金丝雀 = 全局 `flavor: velites` + worker capacity 压低起步，逐步
  恢复至 96 并发（一夜 4.3 万节点、99.6%）；
- 升格落地（2026-08-03，PR #20/#21）：runtime 枚举/dispatch/sweeper/runtime
  维度 + Worker UI/预检；审题链路迁 `runtime: velites` 并验证一夜
  （8.4 万节点、97.7%）；
- 金丝雀关闭（2026-08-04，`14ec130f`）：`flavor: velites` 与审题链路
  `runtime: velites` 落为 tracked 默认值。

**worker bundle 与部署**：二进制不打进 bundle（bundle 只带 skill + prompt），
由 worker 镜像或 worker 侧 PATH 提供；Worker 声明某 runtime 前启动预检会
探测对应二进制（缺失即拒启动）。容器部署前置：worker 镜像含 velites 二进制
+ bwrap setuid；容器 seccomp 需放行 `unshare`（见 §5 沙箱小节的运行时要求）。

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
- **压力门禁（未实施）**：并发 RSS/启动延迟基准纳入 stress lane（对照 PoC 基线：单发 RSS
  <30 MB、冷启动 <50 ms）——截至 2026-08-04，`scripts/stress/` 与 CI stress lane
  均无 velites 基准，仍为待落地项；
- **体积预算**：velites 为 Rust crate，不进 Python 体积预算；CI 新增 rust lane
  （`cargo fmt --check`、`clippy -D warnings`、`cargo test`），按路径裁剪；
- **安全**：secret 不上命令行（`ps` 可见）这一条保留；凭据走 0600 配置文件 +
  env 覆盖（已实现，§7），vault 打通为后续扩展；VAULT-SECRET-001 边界不变化——harness 不接触 vault；
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

- **上下文体积护栏（已拍板：pi 对齐截断）**：原疑虑是截断可能伤 agent 表现、且
  阈值缺乏依据。2026-08-01 决策：直接对齐 pi 的成熟策略——2000 行 / 50KB
  （50×1024 字节）双阈值先到即截，read 截头、bash 截尾并落临时文件，提示语与
  pi 一致（细节见 §8）；截断不切断行（bash 末行单行超限除外）。`output_bytes`
  继续记录截断前体积，金丝雀期间观察截断触发率与 agent 表现，必要时再调阈值；
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
