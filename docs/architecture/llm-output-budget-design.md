# LLM 节点单次输出预算与触顶续写（#952）

> 状态：P0（显式输出预算参数 + 触顶失败归因 + 日志告警）已在 0.7.17 落地；
> §4 的「触顶自动续写」是**设计草案，未实施**，§5 列出需要 owner 决策的点。

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
   OpenAI 兼容路径作为请求体 `max_tokens` 下发（未配置时保持原样不发）。
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
   事件扫描里统计 `stopReason=length` 次数（`shared/output_truncation.py`
   `OutputTruncation`）。仅当**声明产物缺失**、且退出确由产物缺失造成——exit 0
   （pi 正常退出、Host 判缺产物），或 exit 1 且事件流含 velites 的
   `outputs_validation`（产物契约退出；pi 的 exit 1 是进程失败，不归因）——
   并且没有更直接的原因（未恢复的模型调用错误、`agent_end.reason=budget_exceeded`）
   时，失败原因改为
   `Model output hit the per-call output token limit (stopReason=length, Nx) and declared outputs are missing: …`
   并给出配平手段。触顶但产物齐全的 run 仍判完成；崩溃、超时等其他退出码保持
   原归因。失败分类新增 `technical / output_truncated`。Anthropic 的
   `model_context_window_exceeded` 同样映射为 `length`，事件流无法区分，故文案
   同时提示上下文窗口溢出的可能。
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

## 4. 触顶自动续写（草案，未实施）

### 方案 A（推荐）：velites agent loop 内的「触顶续写轮」

触顶后不结束，而是在同一 run 内追加续写轮：

1. **清理模型上下文**（必需，否则下一请求被 provider 拒绝）：截断回复中未完成
   的 toolCall 必须从发往模型的历史里剔除——Anthropic 与 OpenAI 都要求每个
   tool_use 都有对应的 tool_result；Anthropic 截断的 thinking 块可能缺少
   signature，同样需剔除（`provider_data.anthropicThinkingBlocks`）。session
   镜像与事件流保留原样，不改写已发生的事实。
2. **注入续写提示**（user 消息）：说明上一条回复因单次输出上限被截断、未完成
   的工具调用未执行；要求缩短思考、把产物拆成多次小块写盘、已落盘部分不要重写。
3. **有界**：连续触顶达到上限 N（建议 2）后按现状收尾（进入产物补救/退出）；
   续写轮计入 `max_turns` / `max_tokens` / 墙钟预算，不另开额度。
4. **事件面**：不新增事件类型，复用 `message_end.stopReason=length` 作为可观测
   证据；若需要在 `agent_end` 上标注，必须同步 `velites/schema/events.schema.json`
   与契约测试；禁止引入 delta 事件。

已落盘的部分天然保留（工具调用写盘是增量的），因此这就是 issue 方向 1 的
「已生成部分保留、继续生成剩余部分」在 agent 粒度的实现。需要 cargo 环境
补齐 stub provider 的触顶 fixture 测试（`velites/tests/`）。

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

1. 续写（方案 A）是否默认开启，还是按节点开关（例如 config 键）启用？
2. 连续触顶上限 N 取值；续写轮是否计入 `max_turns`？
3. `output_truncated` 是否加入自动重试集合（`TRANSIENT_RETRY_DETAILS`）？
   概率性失败重跑可能成功，但每次重跑都是一次完整的大输出成本。
4. `max_output_tokens` 是否迁到 `execution.*` 由 ExecutionContract 管辖，并在
   Studio 执行面板显式展示（目前经 config_schema 声明）？
5. pi runtime 无对应 flag：是继续静默忽略，还是在 dispatch 时对 pi 节点配置
   该键给出告警？

## Quality Impact

- 正确性：归因只在「触顶 + 声明产物缺失」时改写失败原因，不改变任何 run 的
  成败判定；新参数未配置时 velites 请求体与 argv 均与改动前逐字节一致。
- 可观测性：失败原因、失败分类、job 日志三处都能直接看出是触顶。
- 测试：Python 侧覆盖 argv 映射、事件扫描计数、归因分支（含产物齐全不改判、
  崩溃退出码不改写）、失败分类与日志渲染；Rust 侧 CLI 解析与 OpenAI 请求体
  单测依赖 CI（开发机无 cargo）。
- 风险：旧 velites 二进制不认识新 flag，仅影响显式声明该键的节点。
