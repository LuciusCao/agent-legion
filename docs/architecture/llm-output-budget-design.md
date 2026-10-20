# LLM 节点单次输出预算与触顶续写（#952 / #865）

> 状态：P0（显式输出预算参数 + 触顶失败归因 + 日志告警）已在 0.7.17 落地；
> §4 方案 A「触顶自动续写」已随 #865 实施（0.8.1），§5 决策点 1-3 已定。

## 1. 问题

一个要求「一次产出较大结构化产物」的 agent 节点概率性失败：单次 assistant
回复里 thinking 占绝大部分，思考长度由模型决定，越过单次输出上限
（`max_tokens`）后回复被截断，节点失败；同一输入重跑时成时败。

## 2. 现状事实链（落地前）

| 环节 | 现状 |
|------|------|
| 思考档位 | 节点 `execution.thinking` → `--thinking`（pi / velites 同名 flag，ExecutionContract 中为可选键） |
| velites 思考预算 | OpenAI 兼容路径：档位原样作为 `reasoning_effort`，**无 token 级思考上限**；Anthropic 路径：档位查 models.json `thinkingBudgets` 得 `thinking.budget_tokens`，且必须小于 `maxOutputTokens`（否则请求前报错） |
| 单次输出上限 | Anthropic 路径：Worker 本机 models.json 的 `maxOutputTokens`（缺省 8192）；OpenAI 兼容路径：**请求不带 `max_tokens`**，沿用服务端默认/模型上限 |
| 触顶信号 | provider 把 `max_tokens` / `finish_reason=length` 统一映射成 assistant 消息 `stopReason=length` |
| velites 对触顶的处理 | agent loop 把 `Length` 当普通停止：截断回复里未完成的工具调用**不会执行**；若声明产物缺失，进入一次「产物补救」轮；仍缺失 → exit 1（产物契约） |
| pi 对触顶的处理 | 外部 runtime，exit 0，由 Host 判 `Missing outputs` |
| 失败原因 | `Missing outputs: …` 或 `Agent process exited 1: …`——看不出是触顶，节点作者无从配平 |

结论：thinking 与正文共享**同一个单次输出配额**；平台既没有让节点作者显式
设定这个配额的入口，触顶后也不说是触顶。

## 3. 本次落地（P0）

1. **显式输出预算参数** `max_output_tokens`（velites）：走与 `max_turns` /
   `max_tokens` 相同的节点可调参数解析链（config_schema defaults → 节点
   `config` → workspace 覆盖，intake 冻结，经 `manifest["config"]` 下发），
   `server/app/workflows/velites_command.py` 映射为 `--max-output-tokens N`；
   velites 侧该值覆盖 models.json 的 `maxOutputTokens`（Anthropic），并在
   OpenAI 兼容路径作为请求体 `max_tokens` 下发（未配置时保持原样不发；推理模型
   可在 models.json 模型条目声明 `outputTokensParam: max_completion_tokens` 改用该字段，
   #1093，见 [velites-model-registry.md](velites-model-registry.md)）。
   与累计预算 `max_tokens`（整个 run 的 usage 累计）是两回事。节点声明示例：

   ```yaml
   config_schema:
     type: object
     properties:
       max_output_tokens:
         type: integer
         minimum: 1
         default: 32000
   ```

   pi runtime 无对应 flag，配置后被忽略（与 `max_turns` / `max_tokens` 一致）。
2. **触顶失败归因**：Worker 结果准备（`worker/upload/prepare.py`）在同一遍
   事件扫描里记录**最后一次** assistant 模型调用的 `stopReason`（另累计
   `stopReason=length` 次数供文案使用，`shared/output_truncation.py`
   `OutputTruncation`）。仅当**最后一次调用以 `length` 结束**、**声明产物缺失**、且退出确由产物缺失造成——exit 0
   （pi 正常退出、Host 判缺产物），或 exit 1 且事件流含 velites 的
   `outputs_validation`（产物契约退出；pi 的 exit 1 是进程失败，不归因）——
   并且没有更直接的原因（未恢复的模型调用错误、`agent_end.reason`（`budget_exceeded` / `cancelled`）、velites 自检 `missing` 为空、
   `outputs_validation.violations` 里缺文件之外的契约违例，如 skill contract 无法解析）
   时，失败原因改为
   `Model output hit the per-call output token limit (stopReason=length on the last model call; Nx in this run) and declared outputs are missing: …`
   并给出配平手段。触顶但产物齐全的 run 仍判完成；崩溃、超时等其他退出码保持
   原归因。失败分类新增 `technical / output_truncated`。Anthropic 的
   `model_context_window_exceeded` 同样映射为 `length`，事件流无法区分，故文案
   同时提示上下文窗口溢出的可能。
   归因只关联导致最终产物校验 / 退出的**最后一轮**模型调用：#865 起 velites
   首轮触顶后先进入续写轮（§4 方案 A），连续触顶达上限才回落补救轮；续写轮 /
   补救轮以 `stop` / `toolUse` 正常结束仍缺产物时，失败是后续轮没写齐产物，
   调高输出预算也无济于事，不归因为触顶。

   **触顶归因决策表**（`tests/workers/test_worker_upload_truncation.py`
   `_DECISION_TABLE` 逐行参数化）。velites 事件形态：每次模型调用一个 assistant
   `message_end`；瞬态失败的重试先发 `message_end{stopReason=error, errorMessage}` +
   `auto_retry_start`，成功的重试再发一个 `message_end`；未恢复的模型错误发
   `message_end{error}` 后直接结束，**不发** `outputs_validation`（缺产物时 exit 1）；
   触顶（length）先触发一次续写轮（#865，不占用补救轮；续写轮的 user 注入不进事件流，
   序列上只见又一个 assistant `message_end`），连续触顶达上限 2 后按正常停止回落；
   正常停止时缺产物 / 契约违例触发一次补救轮（契约解析失败跳过补救），补救轮或
   预算收尾轮之后发 `outputs_validation`（`agent.rs` 三处发出点：收尾轮以 toolUse
   结束后的循环顶、收尾轮正常停止、无需补救的正常停止），仍缺产物则 exit 1；取消
   由 Worker 记为 130（velites 自身被取消时 exit 0 + `agent_end.reason=cancelled`）。
   「模型错误」列为与 `fold_model_error` 同一 fold 的未恢复错误。排除归因的
   `agent_end.reason` 取值集合即 velites `EndReason`（`events.rs` 的
   `budget_exceeded` / `cancelled`），正常结束不带 reason，故任一 reason 都排除。
   pi 只在 assistant 消息含 toolCall 时继续下一轮（`length` 消息不带 toolCall
   即结束），表中 pi 「length → …」的行都以该次 length 携带 toolCall 为前提。

   velites **保证不会出现**的组合（表中标 ✗ 的行只用来钉住防御行为）：
   - 收尾轮不叠加：预算收尾只在 `wrap_up.is_none()` 时注入（`agent.rs:194-199`），
     收尾轮结束即发校验并 break（`agent.rs:183-190`、`:352-360`），补救轮只在
     非收尾轮的正常停止里注入（`agent.rs:373-379`），续写轮同样不叠加在收尾轮上
     （guard `wrap_up.is_none()`，`agent.rs:336-348`），一次 run 至多一个收尾轮；
     续写轮至多每段连续触顶一次（上限 2，`truncation.rs`
     `MAX_CONSECUTIVE_LENGTH_STOPS`）；
   - #865 起，触顶与后续收尾（补救轮 / 校验）之间必有续写轮的 assistant
     `message_end`（已达连续上限或处于收尾轮时除外）；表中触顶后直接跟收尾产物的
     velites 行（12/15/16/18/34）因此转为防御行，只钉住归因语义；
   - `outputs_validation` 每 run 至多一次，且其后不再有模型调用：三处发出点
     （`agent.rs:187`、`:354`、`:381`）之后都紧跟 `break`，随后只发 `agent_end`
     （`agent.rs:387`）；
   - assistant `message_end` 必带 `stopReason`：成功路径由 provider 映射后必设
     （`provider/anthropic.rs:160-181`、`provider/openai_compat/mod.rs:179-202`，
     缺 stop/finish reason 时转为错误），失败路径为 `error`（`agent.rs:230`、
     `events.rs:378`）；
   - `errorMessage` 不与 `length` 并存：`errorMessage` 只在 `agent.rs:231` 与
     `events.rs:379` 设置，两处都是 `stopReason=error`（`provider/stub.rs` 为测试桩）。

   | # | 运行时 / exit | 模型调用序列（`stopReason`） | 模型错误 | 其他旁证 | 产物 | 期望 |
   |---|---|---|---|---|---|---|
   | 1 | pi / 0 | stop | — | — | 齐全 | completed |
   | 2 | pi / 0 | stop | — | — | 缺失 | completed（Host 判 Missing outputs） |
   | 3 | pi / 0 | length | — | — | 齐全 | completed |
   | 4 | pi / 0 | toolUse → length | — | — | 缺失 | output_truncated（1x） |
   | 5 | pi / 0 | length → toolUse → stop | — | — | 缺失 | completed（最后一轮未触顶） |
   | 6 | pi / 0 | error → retry → length | 已恢复 | — | 缺失 | output_truncated |
   | 7 | pi / 0 | error → retry → length | 已恢复 | — | 齐全 | completed |
   | 8 | pi / 0 | length → error → retry → stop | 已恢复 | — | 缺失 | completed（最后一轮未触顶） |
   | 9 | pi / 0 | length（带 toolCall，pi 才继续）→ error（未恢复） | 有 | — | 缺失 | model_error |
   | 10 | pi / 0 | error（未恢复） | 有 | — | 齐全 | model_error |
   | 11 | pi / 0 | length（带 errorMessage） | 有 | — | 缺失 | model_error |
   | 12 | velites / 1 | length → 补救 length | — | `outputs_validation` | 缺失 | output_truncated（2x） |
   | 13 | velites / 1 | length → 续写 stop → 补救 stop | — | `outputs_validation` | 缺失 | 原 exit 错误 |
   | 14 | velites / 1 | length → 续写 stop → 补救 toolUse（工具执行后校验） | — | `outputs_validation` | 缺失 | 原 exit 错误 |
   | 15 | velites / 1 | length → 补救 error → retry → length | 已恢复 | `outputs_validation` | 缺失 | output_truncated |
   | 16 | velites / 1 | length → 补救 length | — | 契约违例仅 `missing required file` | 缺失 | output_truncated |
   | 17 | velites / 1 | length（契约解析失败，无补救轮） | — | `contract parse error` 违例 | 缺失 | 原 exit 错误 |
   | 18 | velites / 1 | length → 补救 length | — | 缺文件之外的内容违例 | 缺失 | 原 exit 错误 |
   | 19 | velites / 1 | toolUse → 预算收尾 length | — | `agent_end.reason=budget_exceeded` | 缺失 | 原 exit 错误 |
   | 20 | velites / 1 | stop → 补救 length | — | 内容违例 | 齐全 | 原 exit 错误 |
   | 21 | velites / 1 | length → 补救 error（未恢复，无 `outputs_validation`） | 有 | — | 缺失 | 原 exit 错误（model_error 只在 exit 0 采纳） |
   | 22 | pi / 1 | length | — | 无 `outputs_validation` | 缺失 | 原 exit 错误 |
   | 23 | 任一 / 2 | length | — | — | 缺失 | 原 exit 错误（`Agent process exited 2`） |
   | 24 | 任一 / 124 | length | — | — | 缺失 | `Agent process timed out` |
   | 25 | 任一 / 130 | length | — | — | 缺失 | cancelled |
   | 26 (G1) | velites / 0 | length → 补救轮调用中途取消（无 `message_end`） | — | `agent_end.reason=cancelled` | 缺失 | completed（不归因，Host 判缺产物） |
   | 27 (G2) | velites / 1 | stop → 补救 length | — | `outputs_validation`，违例仅缺文件 | 缺失 | output_truncated（1x） |
   | 28 (G3) | pi / 0 | length → toolUse → aborted（无 errorMessage） | — | — | 缺失 | completed（最后一轮未触顶） |
   | 29 (G3b) | pi / 0 | length → aborted（带 errorMessage） | 有 | — | 缺失 | model_error |
   | 30 (G4) | pi / 0 | toolUse → toolResult `message_end` → length → user `message_end` | — | — | 缺失 | output_truncated（非 assistant 不计入最后一轮） |
   | 31 (G5 ✗) | pi / 0 | length → assistant `message_end` 无 stopReason | — | — | 缺失 | completed |
   | 32 (G6) | velites / 1 | toolUse → 预算收尾轮 error（未恢复，无校验、无 reason） | 有 | — | 缺失 | 原 exit 错误 |
   | 33 (G7) | velites / 1 | toolUse → 预算收尾 toolUse（工具执行后校验） | — | `budget_exceeded` | 缺失 | 原 exit 错误 |
   | 34 (G8) | velites / 1 | length → 补救 length | — | `outputs_validation` 无 `violations` 键 | 缺失 | output_truncated |
   | 35 (G9) | velites / 1 | length → 补救重试耗尽 error | 有 | `agent_end.error` | 缺失 | 原 exit 错误 |
   | 36 (G10 ✗) | pi / 0 | error → retry → length（带 errorMessage） | 有 | — | 缺失 | model_error |
   | 37 (G11) | velites / 0 | length | — | `outputs_validation.missing` 为空（velites `exists`，Worker `is_file`，如产物是目录） | 缺失 | completed（缺失非截断所致，Host 判缺产物） |
   | 38 (G12 ✗) | velites / 1 | length → 补救 length | — | `cancelled`，无 `outputs_validation` | 缺失 | 原 exit 错误 |
   | 39 (G13) | pi / 0 | length → toolUse → length | — | — | 缺失 | output_truncated（本次运行累计 2x） |
   | 40 (G14) | pi / 0 | length → error → retry → length | 已恢复 | — | 缺失 | output_truncated（2x） |
   | 41 (C1) | velites / 0 | length → 续写 stop | — | `outputs_validation`（missing 空） | 齐全 | completed（续写恢复，不归因） |
   | 42 (C2) | velites / 1 | length → 续写 length（达上限回落）→ 补救 length | — | `outputs_validation` | 缺失 | output_truncated（3x） |
   | 43 (C3) | velites / 1 | length → 续写 length（达上限回落）→ 补救 stop | — | `outputs_validation` | 缺失 | 原 exit 错误（补救轮没写齐，不归因） |

   文案中的 `Nx` 是本次运行累计触顶次数，归因前提是最后一次调用触顶：
   `(stopReason=length on the last model call; Nx in this run)`。

3. **日志告警**：job 日志渲染把 `stopReason=length` 从「模型调用错误
   stop_reason=length」改为「单次输出触顶」条目，说明 thinking 计入同一预算、
   未完成的工具调用未执行，以及可用的配平手段。即使后续轮次恢复、run 成功，
   该条目仍可见，作为接近上限的告警。

为什么不放进 `execution.*` / ExecutionContract：`execution` 契约目前只管辖
provider/model/thinking 这类「连接与模型选择」键；预算类参数已有 config_schema
解析链先例（可 workspace 覆盖、intake 冻结、manifest 白名单），改走 execution
需要 workflow schema、ExecutionContract、前端生成类型与 Studio 表单同步改动。
是否迁移见 §5。

兼容性：`--max-output-tokens` 需要包含本改动的 velites 二进制；旧二进制遇到
未知 flag 会直接报错退出（velites 刻意不吞未知 flag）。只有声明了
`max_output_tokens` 的节点受影响，部署时经 `ensure-velites.sh` 重建即可。

## 4. 触顶自动续写（方案 A 已实施，#865）

velites agent loop 内的「触顶续写轮」（`velites/src/agent.rs`）：触顶后不结束，
而是在同一 run 内追加续写轮：

1. **清理模型上下文**（必需，否则下一请求被 provider 拒绝）：截断回复中未完成
   的 toolCall 从发往模型的历史里剔除——Anthropic 与 OpenAI 都要求每个
   tool_use 都有对应的 tool_result；Anthropic 截断的 thinking 块缺 signature
   同样剔除（`provider_data.anthropicThinkingBlocks`）；剔除后无任何可见内容时
   留一个标记文本块（空 assistant content 会被 Anthropic 拒收）。实现为
   `sanitize_truncated_history`（`velites/src/truncation.rs`），在**每一次**触顶后
   对模型侧历史副本执行（续写、补救、预算收尾请求都受益）；session 镜像与事件流
   保留原样，不改写已发生的事实。
2. **注入续写提示**（user 消息，`continuation_message`，同模块）：说明
   上一条回复因单次输出上限被截断、未完成的工具调用未执行；要求缩短思考、把
   产物拆成多次小块写盘、已落盘部分不要重写。续写轮不占用 `WrapUp::Outputs`
   补救轮（续写后正常停止仍缺产物时，补救轮照常触发）。
3. **有界**：连续触顶达到上限 N=2（`MAX_CONSECUTIVE_LENGTH_STOPS`，同模块）
   后按现状收尾（进入产物补救/退出）；非 length 停止即重置
   连续计数。续写轮不另开额度：预算检查在每次模型调用前，续写轮计入
   `max_turns` / `max_tokens` / 墙钟预算（预算耗尽时续写收敛为预算收尾轮）。
4. **事件面**：不新增事件类型、不加 `agent_end.reason` 变体，复用
   `message_end.stopReason=length` 作为可观测证据；每次触顶在 stderr 留一行
   痕迹（turn 号 + 输出 token 数 + 连续计数）；Worker 结果准备把
   `OutputTruncation` 的 `count` / `last_stop` 写入 result metadata
   `output_truncation`（Host 落 `agent_execution_requests.outcome_json`，
   补齐「续写恢复后判 completed」run 的 DB 归因面）。禁止引入 delta 事件。

### 方案 B（不推荐）：文本层 continuation

Anthropic 允许以末尾 assistant 消息做 prefill 续写，但开启 extended thinking
时不能这样续接，工具调用 JSON 半截也无法续接；OpenAI 兼容端点没有统一的续写
协议。收益不覆盖主要场景（thinking 触顶）。

### 方案 C：分片输出声明（issue 方向 2）

节点声明产物分片，平台按片多次调用并负责拼接与校验。涉及 manifest、产物契约
与 Host 校验链，属于独立的大设计，等有明确需求再立项。

### thinking 单独预算（issue 方向 3）

Anthropic 路径已有按档位的 `thinkingBudgets`；OpenAI 兼容路径只有
`reasoning_effort` 档位，协议上没有 token 级思考上限，平台无法强制。现阶段
可用的手段就是节点 `execution.thinking` 降档 + `max_output_tokens` 配平。

## 5. 需要 owner 决策的点

1. **已定（#865，2026-10-20）**：续写（方案 A）默认开启，无节点开关——相对
   静默降级是严格改进。
2. **已定（#865）**：连续触顶上限 N=2（达到即回落现状收尾）；续写轮计入
   `max_turns` / `max_tokens` / 墙钟预算，不另开额度。
3. **已定（#865）**：`output_truncated` 暂不加入自动重试集合
   （`TRANSIENT_RETRY_DETAILS`）——概率性失败重跑可能成功，但每次重跑都是
   一次完整的大输出成本；续写落地后重跑收益进一步下降。
4. `max_output_tokens` 是否迁到 `execution.*` 由 ExecutionContract 管辖，并在
   Studio 执行面板显式展示（目前经 config_schema 声明）？**待定**。
5. pi runtime 无对应 flag：是继续静默忽略，还是在 dispatch 时对 pi 节点配置
   该键给出告警？**待定**。

## Quality Impact

- 正确性：归因只在「触顶 + 声明产物缺失」时改写失败原因，不改变任何 run 的
  成败判定；新参数未配置时 velites 请求体与 argv 均与改动前逐字节一致。#865
  续写只改写发往模型的历史副本（session 镜像与事件流保留原始截断消息），
  不改归因规则；续写轮占用既有预算，连续触顶达上限即回落原收尾路径。
- 可观测性：失败原因、失败分类、job 日志三处都能直接看出是触顶；#865 另加
  stderr 逐次留痕（turn + 输出 token 数）与 result metadata
  `output_truncation {count, last_stop}`（Host 落 outcome_json）。
- 测试：Python 侧覆盖 argv 映射、事件扫描计数、按上方决策表逐行参数化的归因
  分支、失败分类与日志渲染、metadata 上报与 Host 白名单解析；Rust 侧覆盖 CLI
  解析、OpenAI 请求体、续写轮的上下文清理/注入/上限/预算记账
  （tests/agent_loop.rs）与真实二进制 + stub fixture 的端到端触顶形态
  （tests/truncation_continuation.rs）。
- 风险：旧 velites 二进制不认识 `--max-output-tokens` flag，仅影响显式声明该键
  的节点；续写行为变化随下一次 velites 落版发布（release 时 bump 版本号）。
