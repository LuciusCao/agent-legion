# 共享资源库（#1553）

共享资源是跨 workspace 复用的长期、多版本输入包。它不是共享 skill、资源市场或 job 产物交接：稳定身份只有 `resource_id` 与显示名，没有用户 alias；每个版本持有一份不可变对象集合，Share 只授权，不复制字节。

## 数据模型与对象身份

schema v107 建立六张表：

- `shared_resources`：稳定资源 ID、来源 workspace、显示名与首次创建幂等键。来源 workspace 仅作审计，不是运行时解析 fallback。
- `shared_resource_versions`：单调整数版本、状态、canonical `manifest_hash`、来源快照、幂等键、复制代次与逻辑删除时间。版本号在资源行锁内分配，不可复用。
- `shared_resource_files`：逐文件 `path / sha256 / size_bytes / content_type`，以及内部目标 key 和来源 material 代次。API 不返回目标/来源 storage key、ETag 或失败内部详情。
- `shared_resource_grants`：资源到指定 workspace 的 `active / revoked` 授权，按 `(resource_id, workspace_id)` 唯一。
- `shared_resource_selections`：每个被授权 workspace 独立选择的精确 `version_id` 与 CAS `revision`。
- `shared_resource_job_pins`：M43 在 run/job accept 时写入的精确冻结引用；逻辑删除守卫据此保护历史执行。

目标 key 为 `shared-resources/{resource_id}/{version_id}/g{copy_generation}/{file_id}`。每个复制尝试使用新代次与新 key，不覆盖旧对象。数据库触发器禁止修改、插入、删除 ready 版本的文件，也禁止修改或物理删除 ready 版本；唯一允许的版本更新是设置 `deleted_at`。

## 创建与不可变发布

创建资源会从同一来源 workspace 的一个 ready material 或 bundle 建立 v1；后续版本只能由资源 owner 从自己的 material/bundle 创建。bundle 的相对路径沿用现有安全校验，单文件 material 使用其 filename。所有路径必须唯一，单版本最多 1000 个文件。

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

版本选择相同版本且 revision 相符是幂等 no-op；实际升级或切回才将 revision 加一。revision 不符返回 409 与 `current_revision`，因此 v1→v2→v1 都能形成显式新选择且不会被旧请求覆盖。workflow YAML 不保存版本、selection revision 或 hash；M43 只声明稳定资源 ID。

## 精确读取、revoke 与删除

`SharedResourcesService.resolve_exact_version` 是后续 run freeze 的内部接口，输入 `(workspace_id, resource_id, version, manifest_hash, for_new_run)`，输出精确版本与逐文件描述符。描述符包含内部 storage key，禁止直接作为 API 响应序列化。

- `for_new_run=True`：grant 必须 active，版本必须 ready 且未删除；hash 不符、 revoked 或缺失均 fail-closed。
- `for_new_run=False`：用于已冻结旧 job，允许 revoked grant 与逻辑删除版本，但仍要求精确 version + hash 和被授权 workspace。

revoke 不修改 selection、版本、对象或既有 job pin，只停止新任务使用。来源 material/bundle 的后续删除、TTL 或 repair 不影响已发布版本，因为字节已复制到资源自有前缀。

版本删除是逻辑删除：在版本行锁内统计所有 workspace 的 selection 与 `shared_resource_job_pins`，任一引用存在即 409；零引用时仅设置 `deleted_at`，响应明确 `physical_objects_deleted: false`。M43 必须在 accept 的同一事务中使用 `insert_job_pin_tx` 写 pin，该 helper 对版本取 `FOR KEY SHARE`，与删除守卫串行化；不得在 feeder/dispatch 回读当前 selection 或 latest。

`shared-resources/` 前缀从材料根孤儿扫描中排除，文件 key 同时并入 `artifact_key_gc` 的全局 DB 引用集。首版不自动物理删除已发布对象或失败残留，后续人工回收必须另做零引用复核与故障恢复，不能直接把该前缀加入材料删除道。

## Quality Impact

- PostgreSQL fresh/v106→v107 upgrade/catalog parity、触发器不可变、授权可见性、源读目标写与 scoped-token 权限矩阵。
- 第 k 文件复制失败、目标核验失败、缺 hash 补算、复制后来源漂移、同 token 重试与伪造 helper 成功的独立 mutation。
- 重复 Share 零复制、目标独立选择、并发 CAS 单一赢家、升级/切回、revoke 新旧 pin 分界、来源删除、selection/job pin 零引用保护与 GC 前缀排除。
- 对象 I/O 不持数据库事务；发布、选择与删除的状态判定和写操作在同一短事务内完成。M43/M44 的 run freeze、Host/Worker 物化与生产 UI 属于后续任务，本切片不宣称这些路径已可用。
