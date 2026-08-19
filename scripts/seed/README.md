# scripts/seed — workflow 种子包导出/导入工具

平台级项目工具：把一套 workflow 定义（DAG、Agent 定义、自定义节点代码、
skill 源锁定）从任意实例导出为可移植的 **种子包**（`seed.json`），再幂等
导入另一个实例。典型场景：把生产实例的 workflow 定义搬到 develop 实例
做开发测试；开源后任何公司都可以用它迁移自己的 workflow 定义。

**工具公开、数据私有**：本目录是通用工具，不含任何业务硬编码（不含具体
workflow key、workspace 名、capability 清单）；种子包是数据，业务种子包
（含业务 IP）留在私有侧、不进本仓库。

## 种子包格式（schema v1）

```json
{
  "schema_version": 1,
  "exported_at": "...", "source": {"database": "host:port/db"},
  "workflows":  [{"key", "label", "description", "origin", "definition"}],
  "agents":     [{"agent_id", "capability", "definition", ...provenance}],
  "node_codes": [{"workflow_key", "node_key", "capability", "code",
                  "code_sha256", "change_note", ...provenance}],
  "skills":     {"sources": {key: {"repo", "ref"}}, "lock": {...}}
}
```

- `agents[]` 只收「capability 被导出 workflow 引用」的 published Agent
  （Agent 目录自 schema v46 起按 workspace 作用域，导出取自源 workspace）。
- `node_codes[]` 按节点解析：`--node-code` 文件覆盖 > 源 workspace 的
  published 版本（多 workspace 不一致会告警跳过）> global 出厂种子版本。
- 历史遗留的 `executors` 顶层键被容忍并忽略：executor 概念已随
  schema v47（P-0.5）退役，非 Agent 路由节点一律跑隐含 code 池。

## 导出（对源库只跑 SELECT）

```bash
uv run python -m scripts.seed.export_seed \
    --dsn "$AGENT_LEGION_DATABASE_URL" \
    --workflow my_pipeline [--workflow other] \
    [--workspace ws_id] \
    [--node-code some_capability=path/to/code.py] \
    [--forbid-import workspace_libs.somelib] \
    --output seed.json
```

- `--workspace` 指定 agent/节点代码的源 workspace；缺省取所有绑定到导出
  workflow 的 workspace。
- `--node-code capability=path` 用本地文件覆盖某 capability 的节点代码
  文本（适用于节点代码的权威源是文件而非 DB 的场景）。
- `--forbid-import` 追加节点代码禁用 import 前缀（默认只禁 `server.app`；
  私有侧可叠加禁掉自己已退役的库）。

## 导入（目标实例 base URL + admin 凭证，幂等）

```bash
# 先空跑看计划（只读）
uv run python -m scripts.seed.import_seed \
    --base-url http://127.0.0.1:8011 --username admin --password '***' \
    --seed seed.json --dry-run

# 正式导入
uv run python -m scripts.seed.import_seed \
    --base-url http://127.0.0.1:8011 --username admin --password '***' \
    --seed seed.json [--workspace "My Team=my_pipeline:entity"]
```

六步：workspace 绑定（已绑定的自动纳入；`--workspace` 仅在无绑定时创建，
blank 模式——schema v50 后 workflow 无注册概念，种子定义经发布流落地）
→ Agent 发布（按 workspace 作用域，内容一致跳过）→ 首版 revision 发布
（仅缺失时）→ 节点代码发布（文本一致跳过）→ skill 源 upsert + relock
（ref 与 lock commit 一致跳过）→ 校验报告（有 FAIL 非零退出）。

幂等语义：每步先内容比较（canonical JSON / 代码文本逐字节 / ref+commit），
一致即 skip，**不产生新版本**；对同一实例连跑两次，第二次 0 个写动作。

## 安全注意

- 导出不含密钥：定义里只有 vault **引用**（连接 key、`secret_ref`），导出
  完成后整个种子包还会过一遍密钥形态扫描（key 名匹配
  token/password/secret/api_key/credential 且值为非空字符串即判失败）。
  凭据本身永远不落 seed.json。
- 导出对源库只读；导入经 HTTP API 走 admin 会话，不直连目标库。
- skill 源里的 `repo` 可以是本地路径——种子包会如实记录源实例的路径，
  跨机器导入时注意路径在目标机器上的含义。

## 已知平台缺口（全新部署引导）

发布 workflow 的**首个** revision 要求每个 code 节点已有 published 节点
代码，而节点代码 API 要求已有 active revision——两者互相前置，因此全新
workspace + custom-code-only 节点目前无法纯经 API 完成首次引导（step 3
会报 "no published node code"）。已有 active revision 的实例（prod →
develop 迁移场景）不受影响；全新部署可先在 Studio 手工首发，或等平台
补引导通道。

## 前置条件

- 目标实例 backend 已启动、存在 admin 用户（新实例先
  `POST /api/auth/bootstrap`）。
- skill relock 需要目标机器本地能解析 skill 源（本地路径 repo 需要有
  checkout；git URL 需要网络与凭据）。
- 运行中的 job 不受影响：intake 冻结 workflow revision 与节点代码版本，
  种子升级只影响新 intake 的 job。
