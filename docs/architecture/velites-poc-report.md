# PoC 报告：pi_agent_rust 替换 Node Pi CLI 作为 headless executor

日期：2026-07-31 ｜ 分支：poc/pi-agent-rust（worktree `.worktrees/poc-rust-pi`）

## 0. TL;DR

**结论：事件流 wire 结构与 Agent Legion 消费端高度兼容，资源优势明显（RSS ≈ 1/6.5，启动 CPU ≈ 0），但存在 3 个需要 fork 修补的缺口：`--no-context-files` 缺失（会注入祖先目录 AGENTS.md）、错误时 exit code 语义不同（rust exit 1 / Node exit 0）、坏模型在启动期硬失败（零事件流）。建议：fork 修补后可作为 velites harness 的 executor 候选；不修补直接替换不可用（context 泄漏是硬性问题）。**

## 1. 二进制

- 版本：`pi 0.1.23 (590d6189 2026-07-28)`，release `v0.1.23` 的 `pi-darwin-arm64.tar.xz`
- 来源：`gh release download --repo Dicklesworthstone/pi_agent_rust`
- SHA256：`cb64e2986c7bf75769fddaf063c23cc183b5de04a2efd42d50b51a2c09d45d60`，与 release asset digest 一致 ✓
- 安装：`bin/pi-rust`（本 worktree 内）；完整 help 存档 `poc/pi-rust-help.txt`
- v0.1.23 确实缺 darwin-amd64 产物，本机 arm64 无碍；若 worker 有 Intel Mac 需源码编译（nightly）

## 2. CLI flag 差异

| Node pi flag（build_command 在用） | rust 版 | 影响 |
|---|---|---|
| `--mode json` `--session-dir` `--no-extensions` `--no-prompt-templates` `--no-skills` `--skill` `--tools` `--provider` `--model` `--thinking` | 全部存在 ✓ | 无 |
| `--name <name>` | **无** | 会话显示名，仅用于人工辨认，headless 无实质影响 |
| `--approve` | **无** | Node 用于信任 project-local 文件；rust 无此概念，且未知 flag 会被静默吞掉并**吞掉后面的值**（`--name X` 会把 X 吃掉），适配时必须从命令行删除，不能保留 |
| `--no-context-files` | **无等价物** | **关键缺口**，见 §3 |
| `-p/--print` | 有 | rust 在 pipe stdout 的 headless 场景未加 `-p` 也正常走 print 模式（本次 PoC 的 skill run 未加 `-p`，行为正确） |

另外注意 rust 版有 `install/update/swarm-*` 等大量子命令和 `--extension-policy`、`--repair-policy` 等 Node 没有的概念，默认发现机制用 `--no-*` 系列可以全部关掉。

## 3. Context-file 行为（关键陷阱）

源码确认（`poc/src/src/app.rs:166-186, 318-352`）：rust 版**无条件**从 cwd 祖先目录 + 全局目录加载 `AGENTS.md`/`CLAUDE.md` 注入 system prompt 的 `# Project Context` 段，**没有任何 CLI flag 关闭**。唯一开关是 `PI_TEST_MODE` 环境变量，但它同时会把 system prompt 里的时间戳替换为 `<TIMESTAMP>`、cwd 替换为 `<CWD>`（`app.rs:192-205`），不能当 `--no-context-files` 用。

实测证据：
- 在含 marker AGENTS.md 的目录运行，让模型复述 marker：输出包含 `UNIQUE_MARKER_XYZ123`，确认注入 ✓
- 定量：同一 tiny prompt（"Reply with exactly: OK"，kimi-k2.6）首条 assistant message 的 `usage.input`：Node（`--no-context-files`）**808 tokens**，rust（worktree 根有 AGENTS.md）**2621 tokens**，多约 1800 tokens/run。

对 Agent Legion 的影响：worker 的 job_dir 在 worktree 内（`data/jobs/...`），祖先链上的 worktree 根 `AGENTS.md` 必然被注入——既浪费 token 又把仓库操作手册泄进每个 run 的 system prompt。**这是 fork 修补的第一优先级**（加一个 `--no-context-files` flag 跳过 `load_project_context_files`，约 5 行 patch）。

## 4. 命令行适配（PoC 补丁，未 commit）

`server/app/workflows/pi_protocol.py` 的 `build_command` 增加 `AGENT_LEGION_PI_FLAVOR=rust` 环境开关：换 binary、去掉 `--name/--approve/--no-context-files`，其余不变。已用 Python REPL 验证两种 flavor 的 argv 均正确。注意：该开关只是 PoC 用途；即使打开开关，rust 版的 context-file 注入仍然存在（§3），所以它不是完整解决方案。

## 5. 事件流逐字段 diff

方法：`poc/diff_events.py` 对两类 run 做结构级对比（LLM 文本不逐字比，只比结构）：

1. tiny prompt（无工具调用）：`poc/noderef/events.jsonl` vs `poc/ctxtest/events_kimi.jsonl`
2. 真实 skill run（`review_subtitles` skill，3 条字幕，cwd=独立 job 目录，触发 read/write/bash 工具）：`poc/skillrun/node/events.jsonl`（1117 events）vs `poc/skillrun/rust/events.jsonl`（1377 events），完整结果 `poc/skillrun/diff_result.txt`

注：develop 的 `data/jobs` 里只找到 1 个含 prompt.md 的历史 run 且是旧 video-hive 格式（exit 143、skill 目录已不存在），run 目录产物会被清理，因此改用当前 skill + build_prompt 格式手工构造夹具。

### 5.1 消费端依赖字段——全部兼容 ✓

逐条核对 Agent Legion 三个消费方：

- `pi_model_error.fold_model_error`：读 message_start/message_end/turn_end 顶层 `message` 的 `errorMessage`/`stopReason`——rust 字段名、位置完全一致 ✓
- `token_usage._extract_usage`：读 `message_end.message.usage.{input,output,cacheRead}` + `provider`/`model`——rust 全部具备 ✓
- `pi_runner`：按 exit code 判失败（exit≠0 → failed）——对 rust 更宽松地兼容（见 §6）

### 5.2 事件类型集合与序列

两边都发出：`session / agent_start / turn_start / turn_end / message_start / message_update / message_end / tool_execution_start / tool_execution_update / tool_execution_end / agent_end`。rust 还支持 `auto_retry_start/auto_retry_end`（源码确认），与 Node 的重试事件同名。

已知良性差异：
- **turn 边界位置**：rust 把 user message 的 message_start/end 放在 `turn_start` **之前**，Node 放在之后。消费端均按事件类型过滤而非位置依赖，无影响。
- **user message 的 content 形态**：Node 是 content blocks 数组 `[{type,text}]`，rust 是纯字符串。assistant message 两边都是 blocks 数组。消费端只读 assistant message 的 usage/stopReason，无影响。
- turn 数/message_update 数不同是 LLM 非确定性，不算差异。

### 5.3 字段级差异（均为超集/元数据，无消费端影响）

- rust 多出：`sessionId`（agent_start/turn_*/agent_end）、`turnIndex`、`turn_end.latencyBreakdown`（很有用的可观测性增益）、session 事件里的 `provider/modelId/thinkingLevel`
- Node 多出：`agent_end.willRetry`、`message.responseId`、`usage.reasoning`（reasoning token 数；`token_usage.py` 不消费）
- `usage.cost.*`：Node 为 int，rust 为 float（消费端不用 cost 字段，由 `token_usage_pricing` 自行计价）
- `tool_execution_start/end`：**零字段差异**（toolCallId/toolName/args/result/isError 全对齐）；`tool_execution_update` 的 `args`/`partialResult` 内容差异来自两边调了不同工具，属正常

### 5.4 其它运行时观察

- rust 在 print/json 模式下**不落 session 文件**（`--session-dir` 目录为空；Node 会写一份 jsonl）。Agent Legion 运行时只把 session_dir 路径记进 run.json，不读内容，无影响；仅丧失 pi 原生 resume 能力（未使用）。
- rust 版 skill 加载（`--skill` + `--no-skills`）工作正常，skill run 产出了全部 3 个声明输出（subtitles_reviewed.srt / subtitles.json / subtitle_review_report.json），bash 工具能以 cwd=job_dir 跑 python3 脚本 ✓

## 6. 错误语义验证

用必失败模型名 `sqai/no-such-model-xyz` 各跑一次（`poc/errtest/`）：

| | Node pi | pi-rust |
|---|---|---|
| exit code | **0** | **1** |
| events.jsonl | 31 行，含 `auto_retry_*` + `errorMessage`；`detect_model_error` 返回 `"Stream ended without finish_reason"` | **0 行**（启动期模型解析即失败，stderr: `Model sqai/no-such-model-xyz not found`） |
| pi_runner 判定 | exit 0 → 事件流扫描出 error → failed ✓ | exit 1 → 直接 failed ✓ |

另一个数据点：rust 对 `doubao-seed-2.1-turbo-2` 的运行时协议错误（gateway 返回非流式 `application/json`，rust 严格要求 SSE）表现为**事件流内 errorMessage + exit 1**。

结论：两条路径在 pi_runner 下都判 failed，**最终语义等价**；差异在于 (a) rust 用 exit code 而非仅靠事件流表达失败——对 pi_runner 是兼容且更保守的；(b) rust 启动期失败时事件流为空，错误详情在 stderr（pi_runner 会抓 stderr 进 error_message，可接受）；(c) **该 gateway 上 doubao 模型对 rust 不可用**（Node 有非流式容错），模型选型需避开或修补。

## 7. 仓库测试与质量门

- `tests/workflows/test_pi_protocol.py` + `tests/test_pi_event_compression.py`：**14 passed**（含 PoC 补丁；补丁不改变默认 node flavor 行为）
- `tests/executors` + `tests/test_pi_runner.py`：**159 passed**（705s，高负载机器）
- 补丁压缩重写后复跑 `test_pi_protocol.py` + `test_pi_runner.py`：**26 passed**；两种 flavor 的 argv 均 REPL 复验正确
- 所有 pi 相关测试均用 fake binary（`echo` / 手写 shell 脚本）mock，**没有任何测试真拉 pi 子进程**，因此无法用 flavor 开关直接驱动 rust 版跑测试
- 质量门 `scripts/check-quick.sh`：backend static lane **通过**（ruff/mypy/architecture 全绿）。过程中修了两个 PoC 自身引入的问题：(a) 克隆的上游源码 `poc/src/` 被项目 ruff 误扫 → 把 `/poc/`、`/bin/` 加入 `.gitignore`（ruff 默认尊重 gitignore，PoC 产物本就不该 tracked）；(b) `pi_protocol.py` 超体积预算（146 > ceiling 134）→ 按架构纪律压缩补丁至 132 行，未抬 ceiling
- frontend lane 未通过：`./node_modules/.bin/openapi-typescript` 不存在（新 worktree 未装前端依赖），与本次改动无关
- 环境前置：本 worktree 需从 develop 复制 `deploy/secrets/agent_worker_register_token` 与 `vault_master_key` 才能跑测试（已复制，未打印内容）

## 8. 性能（本机 load avg ≈ 40，wall time 噪声大，以 user CPU 与 RSS 为准）

单发（tiny prompt + kimi-k2.6，`/usr/bin/time -l`，各 2 次，`poc/perf/`）：

| | Node pi | pi-rust | 倍数 |
|---|---|---|---|
| max RSS | 171.1 / 171.2 MB | 28.1 / 26.6 MB | **≈ 6.3× 省内存** |
| user CPU | 1.25 / 2.11 s | 0.05 / 0.05 s | **≈ 25-40× 省 CPU** |
| wall | 3.9 / 16.9 s（含 LLM 等待+高负载噪声） | 15.7 / 1.7 s | 不可比 |

冷启动（`--version` ×5，无 LLM）：

| | Node pi | pi-rust |
|---|---|---|
| user CPU | 1.52-1.71 s | ≈ 0.00 s（<10ms wall） |
| max RSS | 139-153 MB | 5.8-7.0 MB |

并发（每边 3 并发同 tiny prompt；采样 `ps -axo pid,rss,%cpu,comm`，`poc/perf/ps-*.txt`）：

| | Node pi ×3 | pi-rust ×3 |
|---|---|---|
| 单进程峰值 RSS | 175-183 MB | 26-27 MB |
| 采样期 RSS / CPU | 116-119 MB @ 68% CPU each | 26 MB @ ~0% CPU |
| wall | 4.4-4.8 s | 1.5-2.8 s |
| 3 并发合计 RSS | **≈ 530 MB** | **≈ 80 MB** |

对 worker 的意义：develop 机器常年 30-60 个 pi 进程并发，Node 每进程 ~170MB RSS + 启动即 1.5s CPU，是明显的内存/CPU 放大器；rust 版同负载下内存占用降至 ~1/6.5，启动 CPU 开销几乎归零。

## 9. 结论与建议

**判定：需 fork 修补后可用。** 事件流兼容度高于预期（消费端依赖字段零缺失），性能收益真实且大，但三个缺口必须处理：

1. **P0 `--no-context-files`**：fork 增加 flag 跳过 `load_project_context_files`（`src/app.rs:166`），否则每个 run 注入祖先 AGENTS.md（实测 +1800 input tokens/run，且把仓库手册泄进 prompt）
2. **P1 错误语义对齐**（二选一）：(a) fork 让 rust 在 agent 级错误时也 exit 0 + 事件流 errorMessage（向 Node 对齐）；或 (b) 接受现状——pi_runner 对 exit≠0 本就判 failed，语义等价，只需确认错误详情从 stderr 进入 run.json 的 error_message（pi_runner 已做）。建议选 (b)，零 patch
3. **P2 gateway 兼容性**：rust 严格要求 SSE 流式响应，`doubao-seed-2.1-turbo-2` 在该 gateway 上不可用；worker 模型白名单需标注，或在 fork 里加非流式 fallback
4. 次要：`--name`（会话命名，可放弃）；session 文件不落盘（无运行时影响）；`usage.cost` int→float（无消费）

**给 velites harness 的建议**：rust 版是可行的 executor 候选——单作者/早期项目（v0.1.x）带来的维护风险可用 vendored fork 对冲，patch 面很小（P0 约 5 行）。若 velites harness 的目标场景是高并发 headless 执行，内存与 CPU 收益（6.5× / 25×+）直接转化为单机 worker 密度上限，值得投入一个 fork；决策前先确认上游对 `--no-context-files` PR 的接受意愿，能接受就贡献上游而非长期维护 fork。

## 附：产物清单（均在本 worktree）

> **状态标注（2026-08-04 补注）**：以下产物所指 worktree `.worktrees/poc-rust-pi`
> 已删除，上述产物均不可得，本清单仅作历史记录。

- `bin/pi-rust`、`poc/pi-rust-help.txt`、`poc/dl/`（安装包+校验）
- `poc/diff_events.py`、`poc/skillrun/{node,rust}/events.jsonl`、`poc/skillrun/diff_result.txt`
- `poc/errtest/{node,rust}/`、`poc/ctxtest/`（context 注入证据）
- `poc/perf/`（time -l 输出、ps 采样）、`poc/perf.sh`
- `server/app/workflows/pi_protocol.py`（AGENT_LEGION_PI_FLAVOR PoC 开关，未 commit；132 行 ≤ ceiling 134）
- `.gitignore`（追加 `/poc/`、`/bin/`，避免 ruff 误扫 PoC 产物）
- `poc/src/`（pi_agent_rust 源码浅克隆，用于行为确认与后续 fork patch 参考）
- LLM 调用统计：约 20 次 tiny-prompt 执行，全部走零成本模型 kimi-k2.6（单次 ~1-3k input tokens）
