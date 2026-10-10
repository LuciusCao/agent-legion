# 材料（Materials）与运行（Runs）：现行参考

本文是 job 输入模型（条目 / 材料 / 运行）、材料与 job 产物对象存储、产物直连 URL 版本固定的
**现行参考**（#1103 自两份设计稿抽出）：

- [materials-and-runs-design.md](materials-and-runs-design.md)：输入模型重设计的决策表（D1–D14）、
  用户场景、路线 A 迁移计划、demo seed 迁移、分阶段实施表；
- [artifact-direct-url-pinning.md](artifact-direct-url-pinning.md)：#853 的候选方案对比与
  SeaweedFS 实测。

两份设计稿已移入[历史设计记录](README.md#历史设计记录时点快照仅供溯源)；现行语义冲突时以代码与
`config/architecture/architecture-invariants.yaml`（RUN-FREEZE-001、MATERIAL-ACCESS-001、
MATERIAL-SECRET-001、MATERIAL-BUNDLE-001、MATERIAL-INLINE-OWNERSHIP-001、
EXEC-WORKFLOW-START-001、EXEC-ARTIFACT-STORE-001、EXEC-ARTIFACT-WORKER-001、
EXEC-GENERATION-002）为准。部署与运维步骤见
[materials-storage-deployment.md](../materials-storage-deployment.md)。

## 1. 概念模型

```
条目（item）                     job 的唯一输入，一条目一 job
 ├─ 材料（material）             字节存对象存储，内容寻址，一等资源
 ├─ 文件夹（bundle）             不可变引用式 manifest，成员是普通材料
 ├─ 文本（text）                 提交时落成一份 ready 材料，再按 material 条目解析
 └─ 外部引用（ref）              {connection_key, external_id, params?}，执行时经 connector 拉取

运行（run）                      一批条目 × 一个 workflow 的一次执行
                                  承载 frozen pins、暂停、统计、quality replay
任务（job）                      run 内单条目的执行单元，携带自身输入与冻结配置
```

不变量：

- **一条目一 job，一 job 一实体**；创建 run 只做格式校验、拆行、内容 hash dedup、建 runs + jobs，
  **提交即同步返回、不调外部接口**（text 条目落材料是唯一例外的前置写，见 §2.2）。
- **解析永远在执行时**：ref 的内容拉取、material 的物化都发生在 dispatch / 节点执行阶段。
- 用户面只有「条目、材料、运行、任务」四个概念；job 创建只经 `RunService`。旧
  `POST /workspaces/{id}/job-batches` 路由仍作为兼容 shim 保留（内部转 `RunService`），不要为它
  加新功能。

## 2. 入口契约：start 节点（EXEC-WORKFLOW-START-001）

Workflow 定义恰好包含一个 `type: start` 节点，它是条目类型契约的结构化家：

```yaml
nodes:
  _start:
    type: start                      # 豁免 capability；不得声明 execution/shard/reduce/terminal/config/config_schema
    accepted_item_types: [material]  # 可选；缺省 [material, ref]（向后兼容）
```

- **边约束**：不得有入边、至少一条出边、出边不得带 condition（loader fail-fast）。
- **不执行**：start 永不进入 `job_nodes`，调度器视其为恒 completed；`allowed_nodes`、publish
  校验、节点配置解析一律跳过它。
- **存量兼容**：没有 start 的旧定义由 loader 注入合成 start（缺省全接受、出边指向所有无入边
  节点），parse→serialize 对称，零迁移。
- **创建期校验**：`RunService.create_run` 在任何写库之前按 `accepted_item_types` 拒绝未接受的
  条目类型（400）；前端 AddItemsDialog 按 active revision 的同一契约禁用对应 Tab。
- `bundle` 与 `text` 都不在缺省契约内，存量 workspace 对它们 fail-closed，需显式 opt-in。

### 2.1 bundle 条目（MATERIAL-BUNDLE-001，#156）

文件夹作为一个整体条目创建一个 job：成员文件走常规上传协议（§4.2），全部 ready 后一次创建
调用冻结 manifest（引用式，不复制字节）。manifest 不可变（改成员 = 新建 bundle）；删除双向
守卫（被 bundle 引用的 material 拒删、被 run 条目引用的 bundle 拒删，TTL collector 适用同一
成员守卫）；成员相对路径拒绝控制字符。物化见 §4.3。

### 2.2 text 条目与 `text_input`（MATERIAL-INLINE-OWNERSHIP-001，#761/#764/#813）

- `{type:"text", content, filename?}`：`RunService` 在契约/节点配置/pin 全部校验通过后、run 行
  写入前，把文本落成一份 ready 材料（sha256 内容寻址，`.md`/`.txt`/`.json` 文件名白名单，UTF-8
  ≤ 64 KiB；`services/run_text_items.py` + `jobs/queries/material_inline.py`），再改写成普通
  `material` 条目——下游看到的与手动上传同名文件完全一样。对象存储未配置时整条请求 503。
- 批次事务：多条文本先全部校验并暂存对象（key 含请求独占随机段），再以单个事务提交材料行；
  失败回滚整批并补偿删除本次暂存对象。文本入口从不复活、重定向或修改既有材料行：

  | 事务内当前状态 | 文本提交行为 |
  | --- | --- |
  | hash 不存在 | 插入本次暂存对象对应的 ready 材料 |
  | 并发胜者为 ready | 复用其身份与元数据，清理本次落败对象 |
  | uploading / failed / expired | 整批返回 409，保留原行与原对象 |
  | 预检查选中的 ready 行已删除、换身份或变为非 ready | 整批返回 409，不复用旧快照 |

- start 节点可选 `text_input` 块（`workflows/start_text_input.py`）：`{label, filename, template}`
  配置输入框标题、默认文件名与预填模板；显式条目文件名优先，未提供时依次回落配置与 `需求.md`。
  不勾选 `text` 时配置惰性。
- 残余：进程崩溃或持续清理失败留下的孤儿暂存对象需运维回收。

### 2.3 条目级 `client_token`（#813）

material / bundle / text 条目可选带 `client_token`（1–64 字符 `[A-Za-z0-9._-]`，首字符为字母或
数字），候选身份变为 `source_id = <id>~<client_token>`：同内容不同 token 各成独立 job、同 token
重提幂等命中同一 job。`input_json` 不携带 token；不带 token 的条目身份与此前逐字节相同。ref
条目不收 token（422）。实现：`services/run_item_client_token.py`。

## 3. 数据模型

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `materials` | `workspace_id`、`content_hash`（`(workspace_id, content_hash)` 部分唯一索引，空串不参与）、`filename`、`content_type`、`size_bytes`、`storage_key`、`status`（uploading / ready / failed / expired）、`expires_at` | 上传即 dedup |
| `runs` | `workflow_key`、`status`（汇总）、`frozen_pins_json`（node_code_versions / agent_versions / node_profiles / quality_replay）、`stats_json`、`queue_payload_json`（异步建 job 的工作状态）、`created_count` / `error_message` | 由旧 batch 表迁移而来，历史行保留原 id |
| `jobs` | `input_json`（`{type:"material", material_id}` \| `{type:"ref", connection_key, external_id, params?}` \| `{type:"bundle", bundle_id}`）、`frozen_config_json`、`run_id` | `source_type/source_id` 只作展示与去重身份（ref 身份按连接限定），不承担输入寻址 |
| `material_bundles` / `material_bundle_members` | bundle：`name`、`total_size_bytes`、`file_count`；成员：`material_id`、`path`（剥公共前缀）、`ordinal` | 成员级联删 |

冻结配置与 pins 以 job / run 列为权威，禁止回读旧 batch payload 形态（RUN-FREEZE-001）。

## 4. 存储层

### 4.1 对象存储与凭据（MATERIAL-SECRET-001）

材料与 job 产物统一走 S3 兼容对象存储（默认 SeaweedFS，#340；RustFS 为存量逃生舱），代码只对
S3 API 编程，可平行换 MinIO / Garage / Amazon S3。**无 filesystem fallback**：开发机共享一个本地
对象存储实例，按 worktree 名派生 bucket（`scripts/init-worktree.sh`）；测试在存储客户端接口上
打 test double。凭据是平台基础设施，env-only 注入（`AGENT_LEGION_S3_*`，同 `database.url`
模式），不落 tracked yaml / API / 日志；业务 connector 凭据仍走 `external_connections` + 实例
vault，两者不混。

### 4.2 上传协议

```
POST /api/workspaces/{id}/materials/presign   {filename, size_bytes, content_hash?}
   → {material_id, upload_url}                  已存在同 hash 直接返回 ready
PUT  upload_url                                  浏览器 → S3 直传（单 PUT presign）
POST /api/workspaces/{id}/materials/{mid}/complete  服务端校验 size/hash → ready
```

SigV4 presigned PUT 无法约束 Content-Length，size/hash 一律由 complete 时服务端 HEAD + 流式
校验强制。后端用内部 endpoint（`AGENT_LEGION_S3_ENDPOINT`）做 HEAD/GET/DELETE；签发给浏览器 /
remote worker 的 presigned URL 用 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`（未配置回落内部
endpoint）——URL 必须以客户端实际可达的地址签发，不能签后改写 host。文件夹上传逐文件走同一
协议。

### 4.3 节点访问：物化 + 内容寻址缓存（MATERIAL-ACCESS-001）

dispatch 按 material `content_hash` 查本地材料缓存，未命中则从对象存储流式下载；缓存目录静态
进沙箱 `--allow-read`，节点读本地只读文件（`ctx.material`），禁止动态放行任意宿主路径。缓存有
容量上限（实例设置），淘汰不影响正确性。bundle 成员按各自 hash 物化后按 manifest 组装**硬链接
目录树**，地址由成员集合确定性派生（`shared/material_bundle.py`，Host/Worker 同一规则）；逐成员
物化全程 pin 住全部成员与树根，淘汰按 entry 粒度原子删除（「目录存在即完整」）。Worker 经 claim
注入的 presigned 通道逐个拉取。

### 4.4 job 产物（EXEC-ARTIFACT-STORE-001 / EXEC-ARTIFACT-WORKER-001）

- 对象存储是 job 产物的权威副本，`job_artifacts` 表为权威清单；job_dir 只是执行暂存与可淘汰
  缓存：淘汰只删清单已确认的文件（带 content_hash 时复核本地 sha256），只限 completed 且无
  活跃 lease 的 job，容量 `AGENT_LEGION_JOB_CACHE_MAX_BYTES`（默认 50 GiB）。
- 节点完成即传；上传失败不 fail 节点，本地副本保留由后台 reconciler 补传；S3 未配置时全特性
  惰性关闭。产物清单维持节点声明制（`outputs`）。
- Worker 产物回传与材料下发走同一条 presigned 通道（随 claim 内存态注入，过期时间
  `max(3600, timeout_seconds + 900)`），Host HEAD 核验（体积上限 `agent_workers.max_archive_bytes`）
  后登记；直传失败回落 legacy CAS（`/api/artifacts`，deprecated）。本地 rerun 沙箱运行前从对象
  存储回填缺失的声明输入（`executors/artifact_restore.py`）。
- 读路径：本地缓存命中直读、缺失回退对象存储；quality artifact_contents 刻意 manifest-first，
  直接读对象存储持久化记录。

### 4.5 产物 key 布局与直连 URL 版本固定（#853）

```
jobs/{workspace_id}/{job_id}/{name}                    # #853 前（存量，只读）
jobs/{workspace_id}/{job_id}/.v/{version}/{name}[.gz]  # #853 起每次写入
```

- 外部产物清单（`GET /api/workspaces/{ws}/jobs/{job}/artifacts`）签发的 presigned `download_url`
  指向清单行 `storage_key`。每次写入落一次性版本 key（`version` = 本次写入的 attempt uuid），
  清单行改指新 key，因此**此前签发的 URL 只会返回签发时那个版本的字节或 404，绝不返回重跑
  新字节**；raw 端点「名字下的当前产物」语义不变。
- `.v` 点前缀段不与任何可服务产物名撞名（下载白名单拒绝点前缀段）；版本 key 仍在 job 前缀内，
  前缀核对、lifecycle、`scripts/gc-s3-jobs.py` 不变。单一来源：
  `services/job_artifact_versions.py` 的 `artifact_version_key`。
- 写面集合不变（EXEC-GENERATION-002）：字节仍只经 promote primitive，清单行仍只经
  `upsert_artifact_row_tx`，只是调用方传入的 authority key 变为版本 key（本地上传 lease 臂 /
  无 lease 臂、Worker 回传 `remote_artifact_promote.promote_all`）。
- **被取代对象清理**：登记事务内 upsert 之前，`executors/_artifact_supersede.collect_superseded_tx`
  以 `select ... for update` 取同 `(job_id, node_key, name)` 行当前 key；提交后
  `discard_superseded_objects` 走 `delete_objects_guarded`（`artifact-authority:<key>` try-lock +
  锁内复核仍被引用则跳过），永不抛出。
- 存量：无迁移，存量行保持固定 key，读路径一律按行 `storage_key`；Worker 协议不变；回滚 Host
  版本可读版本 key 行。

残余面：

| 残余 | 结局 | 兜底 |
| --- | --- | --- |
| 并发**首次**登记同一 `(job, node, name)` | 先提交者的版本 key 成孤儿；已签发 URL 只返回其自己的字节 | GC / lifecycle |
| 被取代对象删除失败 / 锁被在途 promote 持有 | 旧 key 残留，只承载旧字节 | GC / lifecycle |
| 闸拒 / 中途失败的 promote | 新版本 key 孤儿（无行、未签发） | GC / lifecycle |
| 被取代对象删除前的进行中读取 | 连接中断或 404 | 调用方重取清单（[workspace-api-tokens.md「读取产物」](../workspace-api-tokens.md#读取产物清单直连下载与全链路示例)） |

### 4.6 材料 TTL

实例设置 `materials_ttl_days`（默认 0 关闭）→ complete 写 `materials.expires_at` → sweeper 到期
翻 `expired`、引用计数为 0 且过 grace 才物理删除；bucket lifecycle 为孤儿兜底（运维细节见
[materials-storage-deployment.md](../materials-storage-deployment.md)）。

## 5. Connector

connector 不是独立实体类型：连接配置 = `external_connections` 条目（endpoint + 凭据入实例
vault，SECURITY-EXTERNAL-CONNECTION-001），ref 的 `connection_key` 直接引用；解析逻辑 =
workflow 首节点的节点代码（`ctx.service_config(key)` + `workspace_libs/http_client.py`），随
node_code 发布流版本化。用户提交条目时不选 connector。

## 6. 未实施 / 残余面

- 场景 A 本地路径输入、场景 C 服务器侧素材库原地引用、大文件 multipart 与签名级 size 约束。
- workflow 输入契约里的文件类型白名单与 ref 绑定连接 key；`external_connections.direction`
  （source/sink）字段与服务端方向校验（原 CONNECT-DIRECTION-001 候选，未注册）。
- connector 实体化（`versioned_entities` 第三种实体类型），等第二个真实复用者出现再做。
- 需求文本 + 附件合成一个 bundle 条目；异步建 job 的进度与结果分布可见性（方案待讨论）。
- 打包重设计与出站回传见 Issue #120。

## 7. 测试锚点

`tests/services/`（RunService、材料 dedup、text 条目批次事务、`test_artifact_direct_url_pinning.py`）、
`tests/routes/`（条目 API、上传协议、`test_runs_client_token_api.py`）、`tests/executors/`（物化缓存
与沙箱 allow-read）。
