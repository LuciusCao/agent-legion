# velites M2 联合验证报告（真 gateway 对照）

日期：2026-07-31 ｜ 分支：`feat/velites-harness` ｜ 环境：`.worktrees/velites`

验证方式：Node pi（生产同款 flag 序列）与 velites 各跑一次同一 `review_subtitles`
任务（3 条字幕、同一 prompt 模板、同模型 `kimi-k2.6`、`--thinking low`），
再跑坏模型语义与模型矩阵冒烟。产物归档在 `data/velites-m2/`（gitignored）。

## 1. 事件流结构对照（`scripts/velites_diff_events.py`）

- 两侧均产出全部 3 个声明产物（`subtitles_reviewed.srt` / `subtitles.json` /
  `subtitle_review_report.json`），产出结构一致，末条 `stopReason=stop`。
- 事件类型序列均为合规 turn 循环（`session → agent_start → (turn_start →
  message_start/end → tool_execution_start/end → turn_end)* → agent_end`）。
- 体积：pi 1205 行 events.jsonl（绝大多数为 delta）→ 剥离 delta 后 89 条；
  velites 55 条（零 delta，原生即是消费子集）。
- diff 报告的 pi-only 字段**逐一核对均无 Host 消费方**：
  `usage.{totalTokens,reasoning,cacheWrite,cost.*}`、`message.api/responseId`、
  `session.id/version/cwd`、`agent_end.willRetry`、`message_start` 内嵌 usage；
  user 角色 message 事件（`job_log_renderer.py:121-126` 只处理
  assistant/toolResult，user 事件本来就被忽略）。
- velites-only 字段（`sessionId`、`turnIndex`、`tool_execution_end.output_bytes`）
  均为新增，符合「字段只增不改」策略。
- token 对照：pi 10 条 assistant 消息 in=48618/out=1176；velites 9 条
  in=26031/out=787（turn 数差异属模型行为正常波动，非协议问题）。

## 2. 坏模型语义（对齐 pi_model_error.py）

- `no-such-model-404`：`message_end.message` 带 `stopReason=error` +
  `errorMessage`，**exit 0**——与 Node pi 最终语义一致。
- 验证中发现 deepseek gateway 对坏模型返回 HTTP 200 + 非标准错误体
  `{"code":1,"msg":"model error.","data":{}}`。已在 `openai_compat.rs` 增加
  `extract_error_detail`（兼容 OpenAI `error.message` 与 gateway `code/msg`
  两种形态），errorMessage 从模糊的 "response has no choices" 改善为
  "HTTP 200 error body: code=1: model error."。

## 3. 模型矩阵冒烟（最小调用，thinking=low）

| 模型 | 结果 |
|---|---|
| kimi-k2.6 | ✅ stop / exit 0 |
| deepseek-v4-flash | ✅ stop / exit 0 |
| doubao-seed-2-pro | ✅ stop / exit 0 |
| doubao-seed-2.1-turbo-2 | ❌ gateway 自身返回 `{"code":1,"msg":"model error."}`（流式与非流式同） |

重要修正：PoC 报告的 P2（"doubao 只回 application/json，rust 严格要求 SSE"）
实为**该模型在 gateway 上已整体不可用**，与客户端/协议无关（Node 路径同样
失败）。worker 模型白名单应直接剔除 `doubao-seed-2.1-turbo-2`。

## 4. 两个保守默认的复核结论

- `cacheRead`：该 gateway 不返回任何 cache 字段（pi 与 velites 均为 0），
  现有三级字段映射（`prompt_cache_hit_tokens` → `cached_tokens` →
  `prompt_tokens_details.cached_tokens`）保留，无副作用。
- `thinking`：`reasoning_effort` 透传被 gateway 接受（kimi-k2.6 全程正常），
  维持现实现。

## 5. 遗留事项

- ~~重试可观测性~~ **已解决**：velites 重试现在发 pi 兼容事件对——失败 attempt 的
  assistant `message_end`（`stopReason=error`）+ `auto_retry_start`
  （`velites/src/provider/retry.rs` 回调 + `velites/src/events.rs`
  `retry_attempt_events`），Host `pi_event_scan.py` allowlist 已同步保留该事件。
- pi 的 `session.id/version/cwd` 字段虽无消费方，如未来需要可在 velites
  `session` 事件补齐（当前用 `sessionId`）。
