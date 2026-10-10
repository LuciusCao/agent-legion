# 节点 SDK 与 code 节点执行（现行参考）

本文是 code 节点执行链、节点 SDK 与 Worker code 执行协议的**现行参考**（#1103 自
[node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md) 抽出）。
设计过程、批次计划、迁移前盘点与已取消的批次 3 见该设计稿（历史设计记录）；代码注释里
引用的设计稿章节号（§3/§5/§7.2/§10）仍可在该设计稿中解析。现行语义冲突时以代码与
`config/architecture/architecture-invariants.yaml`（EXEC-CODE-002/003/004、
EXEC-CODE-WORKER-001、EXEC-CODE-MANIFEST-001、EXEC-CODE-POOL-001、CONFIG-MANIFEST-001、
VAULT-SECRET-001）为准。

## 1. 执行链

所有节点代码以本 workspace 的 DB 发布文本执行（`versioned_entities`，无 global 兜底；
demo 的 code 节点由 `workflow_nodes/` 种子源经 `services/demo_node_seed.py` 发布进 demo
workspace，`workflow_nodes/` 本身不是执行路径），一律经 velites 沙箱执行（EXEC-CODE-003，
沙箱不可用即拒绝）。Host 与 Worker 是两条**独立入口**：

| 入口 | 链路 | 预取与 auth 失败标记 |
| --- | --- | --- |
| Host 本地 code 池 | `server/app/executors/code.py` → `_code_sandbox.py` | `executors/_code_runtime.py`（`build_runtime` 预取；子进程退出后处理标记） |
| 远程 Worker（kind='code' claim） | `worker/code_runner.py`（Worker 镜像只含 `worker/` + `shared/`，不含 `server/app`） | runtime dict 由 claim 响应 manifest 的 `runtime_context` 在 Worker 侧重建；标记由 `code_runner.py` 自行处理 |

两侧只共享 `shared/` 下的模块：`shared/code_sandbox.py`（velites argv、子进程 env、read
roots、结果/错误解析；bundle / 标记路径等常量单一定义在 `shared/code_contract.py`、经
`code_sandbox` 再导出）、`shared/material_cache.py`（材料缓存目录名、物化错误类型）、
`shared/protocol.py`（注册协议版本常量，跨侧一致性由 `tests/workers/test_protocol_sync.py`
钉死）。改沙箱或运行时行为须两条入口一并核对。

派发顺序：远程 code Worker 在线且 payload 合格时优先远程，否则回落 Host 本地 code 池
（容量 = 实例设置 `code_capacity`；0 = 纯控制面模式，无本地回落，见
[backend.md](backend.md) 的实例设置说明）。

## 2. 节点运行时契约

节点只依赖「预取输入 + job_dir + config」，runtime 键集合（Host / Worker 一致）：

```
job_dir, log_path, inputs, expected_outputs, capability, node_key,
workflow_key, execution_id, workspace_id, workspace, job,
settings_config, node_config, cancellation,
root_dir          # Host 根目录（节点解析机器相对资源路径用；Worker 不下发）
job_batch        # 父进程预取（有 batch_id 且父进程有 DB 时）
skill_versions   # 父进程预取（node_key -> skill_version）
```

- 节点运行时**不含 DB 句柄或 DSN**（EXEC-CODE-004）：DB 派生输入由父进程预取；
  `settings_config` 按段白名单下发，vault/auth/database/agent_workers 段永不越界。
- 预取失败语义：batch 预取失败即抛错；skill versions 预取失败降级为 `{}`。
- **特权动作留在父进程**：节点只记录事实，父进程执行。`ctx.report_auth_failure()` 在
  `job_dir/.node_runtime/auth_failure` 写标记（内容为 node_config 里的 connection key，
  可为空串）；父进程在子进程退出后检查标记，经 `ConnectionTokenService` 失效该连接的
  缓存 token 并删除标记，运行前先做 stale 清理。`.node_runtime/` 是目录，不会被产物
  顶层文件清单捞走。失效动作发生在「节点退出后、下次 dispatch 前」，与连接 token
  缓存的读取时机等价。

## 3. 节点 SDK（`workspace_libs/node_sdk.py`）

分层约束：SDK 只依赖 stdlib（+ 同包 `workspace_libs`），**禁止 import `server.app.*`**——
它是执行面代码，Host 与 Worker 共用。executor 入口签名不变：`run(job, job_dir, runtime)`；
SDK 是节点内部的适配层，不是新的执行协议。

```python
from workspace_libs.node_sdk import NodeContext, entrypoint

@entrypoint                      # 推荐入口；经典 run(job, job_dir, runtime) 签名继续受支持
def run(ctx: NodeContext) -> None:
    ctx.job / ctx.job_dir / ctx.logger
    ctx.config                   # node_config（dispatch 已合并 defaults/workspace/vault/连接注入）
    ctx.service_config(section=None, legacy_keys=())   # settings 段打底 + 连接注入 + 节点覆盖
    ctx.artifacts.read_json(name) / read_json_object(name) / write_json(name, payload)
    ctx.artifacts.read_text(name) / write_text(name, text)
    ctx.checkpoint()             # 取消检查；artifact 写前自动 checkpoint
    ctx.batch / ctx.batch_payload
    ctx.material                 # material 类 job 输入物化后的本地文件
    ctx.root_dir / ctx.skill_versions
    ctx.workflow_manifest(default_key="")
    ctx.report_auth_failure()
```

框架层姊妹模块（同属 `workspace_libs`、同一 import 闭包白名单）：`http_client.py`
（`HttpServiceError(auth_failure=...)`、`fetch_json` 等，按 `service` 标签参数化、不含业务
语义）、`download.py`（`validate_download_url` SSRF 守卫——主机名在请求时才解析，DNS
rebinding 不在此拦截——与流式落盘下载）、`media.py`（`parse_srt`、`get_video_duration`）。

取消语义：`checkpoint()` 对 runtime 里的 token 鸭子类型调用 `raise_if_cancelled()`，SDK 不
自定义异常类型。

兼容策略：SDK 随任务从 Host 下发，版本始终与 Host 一致；自定义节点冻结的是代码文本而非
SDK 版本，SDK 承诺向后兼容（只加不减，破坏性变更走 `NodeContext` 新方法名），契约测试
`tests/workflow_nodes/test_node_sdk.py` + 沙箱 import 契约测试是强制闸。SDK 之前的旧式
节点代码不做大爆炸迁移（版本不可变），兼容 shim 保旧 import 路径可跑；shim 退役是显式
决策（会断老 job 冻结代码的重放）。

## 4. Worker code 执行协议

- **通道**：复用 agent claim 协议（`kind: "code"`），共享 bundle 分发、artifact staging、
  状态回报、心跳与取消通道；manifest 的 code 负载为独立 section（与 agent 负载构成 tagged
  union）：`capability`、解析后 `node_config`、代码文本 + 内容哈希、`expected_outputs`；
  inputs 走 artifact staging。
- **容量池**：Worker 按 kind 分别声明容量（agent / code 各自上限，`max_code_concurrency`
  经 worker 控制台热更），Host 分开记账、分开强制（`server/app/agent_broker/claim.py`）。
- **代码分发**：代码文本 + 内容哈希随任务下发，Worker 零 repo 依赖（只需 Python + velites
  二进制）。前提是节点自足——只能 import `workspace_libs` + stdlib（外加 Worker 镜像预装、
  沙箱内可 import 的 `requests`），由静态 import 闭包扫描判定 worker-eligible
  （`server/app/agent_broker/code_eligibility.py`，按 code_hash 缓存，EXEC-CODE-WORKER-001）。
- **manifest 瘦身**（EXEC-CODE-MANIFEST-001）：入队落盘的 `runtime_context` 只保留轻量审计
  引用（job/workspace id + `batch_id`/`batch_hash`，`server/app/agent_broker/code_manifest.py`）；
  完整 job/workspace/batch/skill_versions 在 claim 响应路径从 DB 重建（内存态，随 secret
  注入一同下发，永不落盘），终态 code 行自动瘦身回引用。
- **secret 边界**（VAULT-SECRET-001 的延伸）：只走既有 HTTPS 通道；Worker 侧仅内存驻留，
  不落盘、不进日志；节点只能拿到自身 config_schema 声明的连接键。
- **取消**：Host 在轮询/回报回复中携带显式取消字段，Worker 收到后 kill 进程组（SIGTERM）。
  时延目标一个轮询周期（秒级）。
- **沙箱二进制**：Worker 解析 velites 时自带副本（`data/bin/`）优先、PATH 兜底；容器部署由
  镜像内置的 `velites-sandbox` 独立 bin 承担 code 池沙箱，见
  [velites-harness.md §9](velites-harness.md#9-与-agent-legion-的集成与切换)。

## 5. 安全边界

- 连接 token（明文）随 `node_config` 进入沙箱子进程与远程 Worker，仅内存（stdin 传递）、
  不落盘；跨进程边界只经既有 TLS 通道。
- 节点运行时没有任何直达 DB 的能力；auth 上报标记是纯文件事实，父进程重新校验 connection
  key 来源后才执行失效——节点本就知道自己用的 connection key，失效自己的缓存 token 不产生
  新权限。

## 6. 配置：节点代码体积上限（#628/#786）

- 配置项 `executor_runtime.workflows.node_code_max_bytes`（`WorkflowsRuntimeConfig`，默认
  64KB、`ge=1024`），admin 实例设置「运行与本地执行」组管理；解析链 **实例设置 > env
  （`AGENT_LEGION_NODE_CODE_MAX_BYTES`）> 默认 64KB**，启动装配时合并、重启生效。非法值：
  env 在 settings 加载时 fail-fast，实例设置 PUT 在契约层 422。
- 校验统一从 settings 取值：`validate_node_code(code, max_code_bytes)` 可注入，`save_draft`
  与 `seed_global` 同一来源；非 DI 构造（worker/测试/种子）回落模块默认 64KB。`publish` /
  `rollback` 按当前上限对「即将发布的字节」复检（publish 经 `expected_hash` CAS 绑定已校验
  草稿；draft 不可作回滚源），上限调低后高上限时期的草稿/版本无法再发布，正在生效的版本
  保持原状。前端经 `WorkflowNodeCodeResponse.max_code_bytes` 展示当前上限。
- 调大的代价：每个版本不可变且永久留存（历史表体积 ×N）、代码文本随每次 claim 全量下发
  Worker、超大单文件损害评审与回滚 diff。上限是逃生门：静态资产优先拆到材料/共享文件。

## 7. 残余面与待办

- code 池内部的快慢细分（长任务与短任务共池）未实施。
- 连接键按节点白名单收束（secret 下发二期）未实施；当前以 config_schema 声明作事实最小下发。
- 存量 published 旧式（SDK 之前）节点版本的只读盘点报告与 Studio「迁移到 SDK」能力未实施。

## 8. 测试锚点

`tests/workflow_nodes/test_node_sdk.py`（SDK 单测）、沙箱 import 契约测试、
`tests/executors/test_code_executor.py`、`tests/workflow_nodes/test_node_self_containment.py`
（节点自足 / import 闭包）、`tests/workers/test_protocol_sync.py`（跨侧协议常量）。
