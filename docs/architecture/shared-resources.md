# 共享资源库（#1553）

共享资源是跨 workspace 复用的长期、多版本输入包。它不是共享 skill、资源市场或 job 产物交接：稳定身份只有 `resource_id` 与显示名，没有用户 alias；每个版本持有一份不可变对象集合，Share 只授权，不复制字节。

## 数据模型与对象身份

schema v107 建立六张表：

- `shared_resources`：稳定资源 ID、来源 workspace、显示名、首次创建幂等键与 `owner_deleted_at` tombstone。来源 workspace 仅作审计，不是运行时解析 fallback；删除 owner 同事务写入 tombstone，同名重建不继承任何 owner 管理权。
- `shared_resource_versions`：单调整数版本、状态、canonical `manifest_hash`、来源快照、幂等键、复制代次与逻辑删除时间。版本号在资源行锁内分配，不可复用。
- `shared_resource_files`：逐文件 `path / sha256 / size_bytes / content_type`，以及内部目标 key 和来源 material 代次。API 不返回目标/来源 storage key、ETag 或失败内部详情。
- `shared_resource_grants`：资源到指定 workspace 的 `active / revoked` 授权，按 `(resource_id, workspace_id)` 唯一。
- `shared_resource_selections`：每个被授权 workspace 独立选择的精确 `version_id` 与 CAS `revision`。
- `shared_resource_job_pins`：M43 在 run/job accept 时写入的精确冻结引用；逻辑删除守卫据此保护历史执行。

目标 key 为 `shared-resources/{resource_id}/{version_id}/g{copy_generation}/{file_id}`。每个复制尝试使用新代次与新 key，不覆盖旧对象。数据库触发器禁止修改、插入、删除 ready 版本的文件，也禁止修改或物理删除 ready 版本；唯一允许的版本更新是设置 `deleted_at`。

## 创建与不可变发布

创建资源会从同一来源 workspace 的一个 ready material 或 bundle 建立 v1；后续版本只能由未删除的资源 owner 从自己的 material/bundle 创建。bundle 的相对路径沿用现有安全校验，单文件 material 使用其 filename。所有路径在来源快照边界统一 canonicalize，发布复核与幂等重试使用同一 canonical 身份，规范化后必须唯一，单版本最多 1000 个文件。

创建流程：

1. 锁外读取来源快照。每个 material 必须 ready 且有 `object_etag`；声明 SHA256 缺失时，按 ETag 条件 GET 流式补算并复核 size。
2. 在短事务中写入资源、`copying` 版本、全部文件 intent、owner grant 和显式 v1 selection。对象 I/O 开始前，目标 key 已被数据库行引用。
3. 锁外调用 M40 唯一复制原语 `copy_objects_verified`：条件复制、目标 HEAD、条件 GET、逐文件 raw SHA256/size 全量核验。service 另对 helper 返回的 source/destination/hash/size 与 intent 逐项比对。
4. 在短事务中锁定版本、复核复制代次，并以 `FOR KEY SHARE` 重查 material/bundle 来源身份；全部一致才把版本发布为 `ready`。

任何文件失败、源缺失、ETag 漂移、目标 hash/size 不符、来源在复制后变化或代次冲突都不能发布。`copying/failed` 版本与尚无 ready 版本的资源对所有读取 API 不可见。失败重试沿用同一 `idempotency_key` 和稳定资源 ID，但推进 `copy_generation` 并使用全新 key；并发旧请求不能覆盖新尝试。幂等键绑定请求 fingerprint，换名称、来源或资源重用同一键返回 409。

Share 在授权时把当时最高 ready 版本写成目标的初始显式 selection；之后创建新版本不会改变它。这不是 `latest`：系统没有 latest 存储、API 或运行时解析。

## API 与权限

所有端点位于 `/api/workspaces/{workspace_id}/shared-resources` 并经过 workspace 鉴权；未授权 workspace 对资源、版本、文件、来源和 hash 均得到相同的 404，列表也不泄露元数据。

| 操作 | 方法与路径 | 权限与语义 |
| --- | --- | --- |
| 创建资源 | `POST /` | 来源 workspace editor；body 为 `name`、`source`、`idempotency_key` |
| 资源列表/详情 | `GET /`、`GET /{resource_id}` | 被授权 workspace viewer；revoked 仍保留管理读取 |
| 创建版本 | `POST /{resource_id}/versions` | owner workspace editor |
| 版本列表/文件详情 | `GET /{resource_id}/versions`、`GET /{resource_id}/versions/{version}` | 被授权 workspace viewer |
| 选择/升级/切回 | `PUT /{resource_id}/selection` | 当前 workspace editor；body 为精确 `version` + `expected_revision` |
| Share | `POST /{resource_id}/shares` | full-session；来源 viewer + 目标 editor；仅建 grant/初始 selection |
| 授权列表 | `GET /{resource_id}/shares` | owner workspace viewer |
| Revoke | `DELETE /{resource_id}/shares/{target_workspace_id}` | owner workspace editor；可重复调用 |
| 逻辑删除版本 | `DELETE /{resource_id}/versions/{version}` | owner workspace editor；零引用才允许，不删对象 |

重复 Share 返回同一 grant 与 `duplicate: true`，对象数量不变。revoked grant 再次 Share 会恢复 active 并返回 `duplicate: false`。scoped token 不能执行任何资源共享写操作。

版本选择相同版本且 revision 相符是幂等 no-op；实际升级或切回才将 revision 加一。revision 不符返回 409 与 `current_revision`，因此 v1→v2→v1 都能形成显式新选择且不会被旧请求覆盖。workflow YAML 不保存版本、selection revision 或 hash，只声明稳定资源 ID。

## Run 版本冻结与 Host 执行（M43）

Host code 节点以 `shared_resources: [<resource_id>, ...]` 声明资源；列表元素只能是稳定 ID，mapping、`version` / `manifest_hash` / `binding_revision`、`latest`、`id@version` 与重复 ID 均在 definition load/publish 前拒绝。非 code 节点不能声明；带资源声明的 workflow 走旧 `/job-batches` 时 fail-closed，必须使用 `/runs`。

同步与异步 accept 都在同一事务内对 grant 和选中版本取 `FOR KEY SHARE`，读取当时显式 selection 并写入 run 的 `frozen_pins_json.shared_resources`。内部 spec 包含 `node_key → resource_id`、精确 `version_id + manifest_hash + grant_id`、来源与逐文件公开元数据、`binding_revision` 和 canonical `pins_hash`。grant 锁与 selection CAS/revoke 串行，版本锁与逻辑删除串行；异步 feeder 只消费 accept 时写入 `target_spec` 的 pins hash，不重新读取 selection。

去重键是原 item 键加 `pins_hash`，资源 job id 对完整 `(workspace_id, workflow_key, source_id, pins_hash)` 取 SHA256，固定小于文件系统 255 字节上限且不截断原始前缀；无资源 job id 保持原编码不变。相同 selection revision 的重复提交命中原 run/job；显式升级、切回（包括 v1→v2→v1）改变 revision 和 hash，因而产生新 run 与新 job。全量重复提交仍保留 #501：同 pins digest 的 failed run 在 job 已补齐时原子恢复 created、清错误并对齐实际 job 计数；成功 run 幂等返回不变。每个 chunk 的 job INSERT 与 `shared_resource_job_pins` 写入同一事务，pin helper 复核 run 已接受的精确身份；accept 后的 revoke 不阻断该 run 后续 feeder 写入，也不能改变旧 pin。版本删除同时把 run spec 视为引用，覆盖异步 accept 后尚无 job 的窗口。

Host dispatch 只按 job pin 读取精确版本，revoked grant 或已逻辑删除版本仍可物化历史执行；不存在 current/latest 回读。文件经内容寻址缓存和只读目录树提供给 `ctx.shared_resources[resource_id]`，节点只见本节点声明的资源、版本、manifest hash、相对/本地路径与文件 hash，不见 storage key、grant 或 selection revision。共享资源首次下载前即计算整个资源集合的文件与目录地址，并把当前 runtime 的普通 material 文件、bundle 目录及其 entries 的精确 `cache_address`（含无 hash 成员的 `material_id-v2gN` 代次地址）纳入同一联合 pin，后续每次淘汰都保护全部 runtime 输入；普通材料与共享资源复用 `material_cache_address` / `bundle_address`，字节仍按需流式下载，不预读整个集合。下载和缓存命中都复核 size/SHA256，缺失、对象漂移、毒化缓存与跨资源淘汰均 fail-closed。普通 rerun 保留原 job pin，quality replay 从原 run 继承 spec 并给副本写入同一精确 pin。workflow upgrade 在 jobs UPDATE 前逐节点比较旧/新 snapshot 的稳定资源 ID 集合；新增、删除、替换或新节点声明都以 `shared_resource_declaration_changed` fail-closed，job/run/status/pin 不变，只有完全一致的声明可升级并继续使用原 pin，绝不按 current binding 补资源或改写旧 pin。损坏旧快照不直接当空声明：无 pin 且目标无声明时才走既有保守 clean/inherit 修复；已有 pin 或目标新增声明而无法证明一致时同样 skipped 且零变更。

run/job 详情的 `shared_resources` 只投影资源名、来源、版本、manifest hash 与文件 path/SHA256/size/content_type；`binding_revision`、pins hash、grant/version 内部行身份与 storage key 不进入 API，主 UI 不暴露去重 revision。

## Worker 远程执行与 Agent 资源准备（M44）

带资源声明的普通/shard code 节点可远程执行，协议下限为 v7。队列 manifest 只持久化当前节点的稳定资源 ID；没有在线 v7 Worker 时仍回退 Host，纯远程部署保持 pending。claim admission 同时核对 manifest 与 job 冻结快照中的当前节点声明，旧 Worker 得到 `shared_resource_protocol_too_old` 并保持 queued，而不是失败后重试或忽略资源执行；描述符组装在同一声明快照上再次拒绝旧 Worker。混合 workflow 中未声明资源的节点不受该协议门影响。

claim 响应只从 `shared_resource_job_pins` 重建当前节点的精确版本，绝不回读 current selection/latest。非空 manifest 声明必须与有效冻结快照中当前节点的资源集合完全相等；快照缺失、空 mapping 或类型无效时不能用 manifest 自称的 ID 降级执行，只有快照自身有声明时才允许 manifest 字段被删除后以快照为准，普通无资源 legacy manifest 不受影响。每个描述符携带 `resource_id / version / manifest_hash` 与逐文件 `path / sha256 / size_bytes / content_type / download_url`；临时 GET 按目标 storage key 现场签发，但 key、grant、selection revision 和内部 version id 不上线，描述符与 URL 只存在于 claim 响应内存。revoke 或逻辑删除版本后，历史 job 仍按原 pin 生成相同描述符。文件集合与 canonical manifest hash 在签发前全部复核，pin 缺失、声明不符、描述符删除或格式降级均 fail-closed。

Worker 在开始下载前验证全部描述符、URL、路径和 manifest hash，再按文件流式物化到与 Host 相同的内容寻址缓存。下载、缓存命中与最终目录树中的每个文件都重新计算 SHA256/size；目录只接受 manifest 精确列出的常规文件，拒绝 symlink、额外/缺失文件和不安全路径。普通 material、bundle 成员及全部资源文件和目录树联合 pin，避免后续文件的 LRU 淘汰破坏本次执行。三个 presigned 下载面共用异常边界，连接打开和流式读取中的 requests/urllib3/OSError 只保留异常类型名并抑制原异常链，traceback、`execution.failed`、rolling log 与结果 `error_message` 均不得携带签名 URL 或凭据。节点 runtime 只得到与 Host 相同的只读资源视图，临时 URL 和描述符不会进入沙箱。

标准 code 节点 `workflow_nodes/shared_resource_prepare.py` 把该视图写成普通产物 `shared_resources.json`，下游 Agent 继续通过现有 `input_artifacts` 获得文件，不需要资源 descriptor、Worker 协议或 Agent 专用 JSON 配置。JSON 的 `resources` 数组保留资源 ID、版本、manifest hash 和逐文件公开元数据：`text/*`、JSON/XML/YAML 等文本类型必须严格按 UTF-8 解码，其他类型使用 base64，统一放在 `encoding + content` 字段。准备节点再次核验本地文件 hash/size，单文件最大 1 MiB、原始字节总量最大 4 MiB、编码后 JSON 最大 8 MiB；声明总量在读取前检查，实际字节与最终文档也分别检查，任一超限或无效 UTF-8 都不写产物。产物不包含本地路径、storage key 或下载 URL，普通 UI 仍只呈现资源已添加和可运行。

## 精确读取、revoke 与删除

`SharedResourcesService.resolve_exact_version` 是精确 pin 的内部接口，输入 `(workspace_id, resource_id, version, manifest_hash, for_new_run)`，输出精确版本与逐文件描述符。描述符包含内部 storage key，禁止直接作为 API 响应序列化；run accept 使用带锁的 selection 快照接口，dispatch 使用 job pin 描述符，二者都不解析 latest。

- `for_new_run=True`：grant 必须 active，版本必须 ready 且未删除；hash 不符、 revoked 或缺失均 fail-closed。
- `for_new_run=False`：用于已冻结旧 job，允许 revoked grant 与逻辑删除版本，但仍要求精确 version + hash 和被授权 workspace。

revoke 不修改 selection、版本、对象或既有 job pin，只停止新任务使用。来源 material/bundle 的后续删除、TTL 或 repair 不影响已发布版本，因为字节已复制到资源自有前缀。owner workspace 删除在同一事务设置 `owner_deleted_at` 并级联删除其自身 grant；Share/恢复授权、版本创建/重试/发布、授权列表、revoke、逻辑删除和创建幂等均要求 owner 未删除，旧资源不能由同名新实例接管。其他目标的 ready grant、显式选择、精确读取与 job pin 不因 owner 删除而失效。

版本删除是逻辑删除：先以 `FOR UPDATE` 锁定 resource 行、与 owner tombstone 串行，再锁版本行并统计所有 workspace 的 selection、`shared_resource_job_pins` 与已冻结 run spec，任一引用存在即 409；零引用时仅设置 `deleted_at`，响应明确 `physical_objects_deleted: false`。job 创建事务使用 `insert_job_pin_tx` 写 pin，该 helper 对版本取 `FOR KEY SHARE`，与删除守卫串行化；feeder/dispatch 不得回读当前 selection 或 latest。

`shared-resources/` 前缀从材料根孤儿扫描中排除，文件 key 同时并入 `artifact_key_gc` 的全局 DB 引用集。首版不自动物理删除已发布对象或失败残留，后续人工回收必须另做零引用复核与故障恢复，不能直接把该前缀加入材料删除道。

## Quality Impact

- PostgreSQL fresh/v106→v107 upgrade/catalog parity、触发器不可变、授权可见性、源读目标写与 scoped-token 权限矩阵。
- 第 k 文件复制失败、目标核验失败、缺 hash 补算、复制后来源漂移、同 token 重试与伪造 helper 成功的独立 mutation。
- 重复 Share 零复制、目标独立选择、并发 CAS 单一赢家、升级/切回、revoke 新旧 pin 分界、来源删除、selection/job pin 零引用保护与 GC 前缀排除。
- 对象 I/O 不持数据库事务；发布、选择与删除的状态判定和写操作在同一短事务内完成。M43 的 sync/async accept、升级/切回去重、revoke 后旧 pin、replay、缺失/hash/缓存毒化与 pin 故障回滚由 `tests/services/shared_resources/test_run_freeze.py` 覆盖；声明、脱敏与 SDK 只读视图由 `tests/workflows/test_shared_resource_declarations.py` 覆盖。
- M44 的真实 pin/selection/revoke claim、同快照旧 Worker 准入由 `tests/services/shared_resources/test_worker_claim.py` 覆盖；描述符删除/降级、对象缺失/hash 错误、文件与目录缓存毒化、Host/Worker 字节一致性由 `tests/workers/test_shared_resource_materialize.py` 覆盖；UTF-8/base64 与三类上限由 `tests/workers/test_shared_resource_prepare.py` 覆盖。`tests/full/test_shared_resource_worker_e2e.py` 走真实 Host dispatch → 旧/新 Worker claim → Worker 子进程 → Agent input artifact 全链路；另以准入、空快照注入、缓存复核和读取前总量预检 mutation 验证测试有效性。R1 增加 `tests/workers/test_download_url_scrubbing.py`，用真实 requests/urllib3 异常验证单 material、bundle、共享资源三条路径及 run/event/rolling-log 边界的签名脱敏，并以 None/空字符串/空 mapping/多节点注入和异常链、ProtocolError、缺失快照 mutation 复验。
