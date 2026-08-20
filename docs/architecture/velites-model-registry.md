# Runtime-owned model discovery and velites provider registry

日期：2026-08-19｜状态：已落地

## Decision

Host 只声明 Agent 节点所需的 `runtime/provider/model`，不持有 provider endpoint、
协议方言或凭据。每台 Worker 对启用的 runtime 运行各自的模型发现 adapter，将发现结果
与本地 `models` allowlist 求交集，并把有效的 `(runtime, provider, model)` 三元组注册给
Host。Host 仅把任务交给声明了同一三元组且 capability 匹配的 Worker。

velites 的单一事实源是 `~/.velites/models.json`（`VELITES_MODELS_PATH` 可覆盖）：

```json
{
  "providers": {
    "sqai": {
      "api": "openai-completions",
      "baseUrl": "https://llm.example/v1",
      "apiKey": "$SQAI_API_KEY",
      "models": ["kimi-k2.6"]
    },
    "anthropic": {
      "api": "anthropic-messages",
      "baseUrl": "https://api.anthropic.com",
      "apiKey": "$ANTHROPIC_API_KEY",
      "anthropicVersion": "2023-06-01",
      "models": [{
        "id": "claude-sonnet",
        "maxOutputTokens": 16384,
        "thinkingBudgets": {"low": 1024, "medium": 4096, "high": 8192}
      }]
    }
  }
}
```

`apiKey` 可为 0600 文件中的字面值；推荐使用精确的 `$ENV` / `${ENV}` 引用。模型发现
会解析凭据引用：引用缺失即探测失败，该 runtime 不广播任何模型，不会领取 Agent 任务。
Docker 部署通过 `VELITES_PROVIDER_ENV_FILE` 指向的独立 0600 env file 把这些引用变量
注入 Worker；默认可选读取 git-ignored 的 `deploy/velites-provider.env`。Compose 自身的
`deploy/.env` 仅负责变量插值，不能替代该容器凭据注入通道。

## Runtime adapters

- velites：`velites models list --json`；
- Pi：`pi --list-models`，其文本方言只在 Pi discovery adapter 内解析；
- OpenClaw：`openclaw models list --json`，只广播 `available != false` 且非 missing 的模型。

一个 runtime 探测失败只降级该 runtime；其他 runtime 和 code 执行池继续工作。运行态
effective models 不回写持久化 YAML。Worker 重启或 runtime/allowlist 配置变更会重新探测。

Worker `models` 字段现在是 runtime-scoped allowlist：

```yaml
runtimes: [pi, velites]
models:
  - runtime: velites
    provider: sqai
    model: kimi-k2.6
```

某 runtime 没有 allowlist 条目时允许它发现的全部模型。旧的 `provider/model` 条目在加载时
扩展到当时启用的全部 runtime。Worker 协议 v3 注册三元组；Host 读取缺少 runtime 的旧行时
按 runtime wildcard 兼容，但所有新 Worker 都发送显式 runtime。

注册响应同时返回 `host_protocol_version`。v3 Worker 拒绝缺少该字段或版本低于 v3 的
Host，因此滚动发布必须先升级 Host、再升级 Worker；这避免旧 Host 接受 v3 请求后静默
丢弃 `runtime`，把仅属于 velites 的 provider/model 错投给 Pi。

## Provider drivers

velites 保留 crate 内的 `Provider` trait，并实现：

- `openai-completions`：OpenAI-compatible Chat Completions、SSE、tool calls、
  `reasoning_effort`；
- `anthropic-messages`：Anthropic Messages API、`x-api-key` / `anthropic-version`、
  content blocks、streamed `input_json_delta`、tool use/result、cache usage、stop reason，
  以及 thinking level 到模型 `thinkingBudgets` 的映射。

Host 下发的 provider 名称原样传入 velites并记录到事件中；不再把 `sqai`、`deepseek` 等
静默改写成 `gateway`。旧 `VELITES_BASE_URL/VELITES_API_KEY` 只作为无 models 文件时
`gateway/openai_compat` 直跑的迁移桥，不参与 Worker 模型发现。

## Quality Impact

- Rust 单元/集成：registry 解析、凭据 fail-closed、模型发现 CLI、OpenAI 兼容回归、
  Anthropic request/message/tool/SSE/usage/stop 映射；
- Worker 单元：每个 runtime 独立命令、allowlist 交集、单 runtime 故障隔离、Pi 文本方言；
- Host 契约：v3 三元组规范化、旧二元声明兼容、同 provider/model 的跨 runtime 隔离；
- 既有 `tests/workflows/test_velites_command.py` 固定 provider 原样传递；
- 交付前执行 `./scripts/check-quick.sh`，纯 Rust 另执行 `cargo test`。
