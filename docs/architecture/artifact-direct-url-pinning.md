# 外部产物直连 URL 固定到不可变产物版本（#853）

状态：已实施（0.7.15）。#838（#739 外部产物 presigned 直连下载）P1 follow-up。

## 1. 问题

#838 起外部产物清单（`GET /api/workspaces/{ws}/jobs/{job}/artifacts`）给
object-backed 条目签发 presigned GET `download_url`，签名目标是清单行的
`storage_key`。#853 之前所有写入都落固定权威 key
`jobs/{workspace_id}/{job_id}/{name}`：同一 job 在 URL 有效期内（默认 1 小时，
最长 7 天）重跑并再次产出同名产物时，promote 覆盖这个 key，**旧 URL 随之返回
新执行的字节**。调用方只能靠清单 `content_hash` 事后校验；吊销 token 后 TTL 内
仍能读到同 job 同名的重跑新字节。

验收：

- 重跑覆盖同名产物后，此前签发的 URL 仍返回旧字节，或返回 404/403，绝不返回
  新字节。
- raw 端点「名字下的当前产物」语义（#508）不变。

## 2. 候选方案

| 方案 | 机制 | 满足验收 | 写入面改动 | 删除 / lifecycle | 存量迁移 | 调用方配合 | SeaweedFS 4.45 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A. 不可变版本 key（**采用**） | 每次写入落一次性 key `jobs/{ws}/{job}/.v/{version}/{name}`，清单行改指新 key；被取代的旧 key 登记提交后删除 | 是（旧 key 不再被写；删除后 404） | 只改 authority key 的派生（2 处入口）+ 登记事务内取旧 key；写面注册表不变 | 现有按行删除、rerun 清理、GC 全部按 `storage_key` 工作，零改动 | 无（存量行保留固定 key，新写入永不回写固定 key） | 无 | 实测通过（§3 E2） |
| B. bucket versioning + `versionId` | 开启 bucket 版本控制，签发带 `versionId` 的 URL | 是（实测旧版本 URL 返回旧字节） | 写面不改，但每个 put/copy 都生成版本，promote 的回滚备份 copy 也产生版本 | **破坏吊销 SOP**：普通 `DeleteObject` 只留删除标记，带 `versionId` 的旧 URL 仍返回旧字节（实测）；job 删除 / rerun 清理 / GC 全部要改成按版本删除，并需要 `NoncurrentVersionExpiration` lifecycle | 需开启版本控制的部署变更；未开启时退回旧语义（两套语义并存） | 无 | 支持（§3 E3） |
| C. 签入 `If-Match: <etag>` | presign 时把 `IfMatch` 签进 URL，覆盖后 412 | 仅对配合的调用方 | 无 | 无 | 无 | **必须**：`If-Match` 是请求头，SigV4 只能把它列进 `SignedHeaders`，调用方不带该头即 403（实测），浏览器导航 / `curl` 默认 / 现有对接方全部失效 | 实测：不带头 403、带旧 etag 412 |
| D. 把 `content_hash` 写进签名覆盖参数 | URL 带一个签名覆盖的查询参数 | 否 | 无 | 无 | 无 | 无 | S3 不按自定义查询参数校验对象内容，签名只防篡改不防覆盖 |
| E. 短 TTL + 清单 `content_hash` 校验 | 缩短 TTL，调用方事后校验 | 否（窗口缩小但仍可能返回新字节） | 无 | 无 | 无 | 需要调用方自行校验 | — |
| F. 签发时复制到内容寻址 pin key | 列清单时 copy 到 `.../{content_hash}/{name}` 再签 | 是 | 新增一个**读路径上的**字节写面（EXEC-GENERATION-002 注册表外），大对象每次签发多一次 copy | 新增 pin 对象的清理面 | 无 | 无 | — |

选择 A：唯一同时满足「验收 + 无调用方配合 + 不改部署形态 + 吊销 SOP 不退化 +
无存量迁移」的方案，爆炸半径限于 authority key 派生与一个提交后清理步骤。

## 3. SeaweedFS 4.45 实测（compose 默认后端）

临时容器 `chrislusf/seaweedfs:4.45 server -s3`（带 `-s3.config` 身份配置，确认匿名
GET 被拒 403，签名真实生效），boto3 SigV4 presign + `requests` 直连：

| 编号 | 场景 | 结果 |
| --- | --- | --- |
| E0 | 匿名 GET | 403（鉴权生效，后续结论不是匿名放行造成的） |
| E1 | 固定 key：签发 → 覆盖写 → 旧 URL | 200 **新字节**（#853 复现） |
| E2 | 版本 key：签发 v1 → 写 v2（另一 key）→ 旧 URL | 200 旧字节；删除 v1 后 404 `NoSuchKey` |
| E2 | 版本 key 与 legacy 固定 key 同 job 前缀并存、server-side copy 落版本 key、`list_objects_v2` 前缀列举 | 均正常 |
| E2b | 文件 `jobs/ws/job/v` 与 `jobs/ws/job/v/t/a.txt` 同名并存（filer 文件/目录撞名） | 均可写可读 |
| E3 | bucket versioning：带 `versionId` 的 URL 覆盖后 | 200 旧字节；**普通删除（删除标记）后仍 200 旧字节**；按版本删除后 404 |
| E4 | presign 签入 `IfMatch` | `X-Amz-SignedHeaders=host;if-match`；不带头 403、带头 200、覆盖后带旧 etag 412 |
| E5 | 篡改 URL 路径里的版本段 | 403（签名不匹配） |
| 端到端 | 生产代码路径（`S3StorageClient` + `JobArtifactObjectStore` + `ExternalArtifactAccessService` + 真 Postgres）：上传 → 列清单 → 直连 → 同名重写 → 旧 URL / 新 URL / raw | 修复前：旧 URL 200 新字节；修复后：旧 URL 404、新 URL 200 新字节且 `content_hash` 一致、raw 返回新字节、job 前缀下只剩一个对象 |

## 4. 设计

### 4.1 key 布局

```
jobs/{workspace_id}/{job_id}/{name}                    # #853 前（存量，只读）
jobs/{workspace_id}/{job_id}/.v/{version}/{name}[.gz]  # #853 起每次写入
```

- `version` 为每次写入的 attempt uuid（本地上传臂与 Worker promote 臂本来就为
  staging / 回滚备份生成 per-invocation attempt，复用同一维度）。
- `.v` 是点前缀段：下载白名单 `is_downloadable_artifact_name` 拒绝点前缀段，任何
  可服务的产物名都不可能与版本命名空间撞名；即便遗留数据里有同名文件，E2b 实测
  SeaweedFS 也允许文件与目录同名并存。
- 版本 key 仍在 `jobs/{ws}/{job}/` 前缀内：读侧前缀兜底（H1）、job 删除前缀核对、
  lifecycle 规则、`scripts/gc-s3-jobs.py` 的对照全部不变。`.gz` 形态标记（#338）
  仍是 key 末尾后缀。
- 单一来源：`server/app/services/job_artifact_versions.py` 的
  `artifact_version_key`；`artifact_storage_key` 保留为存量布局（只剩
  `verify_remote` 的无 execution 臂与测试使用）。

### 4.2 写入面（EXEC-GENERATION-002）

写面集合**不变**：字节仍只经 `_artifact_promotion`（`put_stream_with_retries` /
`promote_to_authority_guarded`），清单行仍只经 `upsert_artifact_row_tx`，
`config/architecture/execution-write-surfaces.json` 无需改动。变化只有调用方传入的
authority key：

| 入口 | 变化 |
| --- | --- |
| `JobArtifactObjectStore.upload` lease 臂（D12 镜像） | authority key = 版本 key；共享 promote primitive 的备份臂找不到既有对象，不产生 `.rollback` 备份 |
| `JobArtifactObjectStore.upload` 无 lease 臂（reconciler 重传、approval 产物） | 直写版本 key（新 key，不覆盖任何对象），再登记 |
| `remote_artifact_promote.promote_all`（Worker 回传） | authority key = 版本 key（+ staging ref 的 `.gz` 标记）；Worker 协议、staging 布局不变 |

promote primitive 的「备份 → copy → 锁内闸 → 恢复」序列保持原样：它与 key 布局无关，
对版本 key 而言备份 / 恢复臂天然空转（key 此前不存在）；闸拒时新版本对象成为无行
引用的孤儿，从未被签发过 URL，由 GC / lifecycle 兜底（与 #853 前首写闸拒的残留
形态相同）。简化或移除这套机制不在本 PR 范围。

### 4.3 被取代对象的清理

清单行改指新 key 后，旧 key 成为被取代对象。若不处理：存储随重登记膨胀，且吊销
SOP「删除 job 即删除其对象」退化（job 删除按行删除，只删当前 key，被取代的旧版本
在 TTL 内仍可经旧 URL 读到旧字节）。处理：

1. **登记事务内**、upsert 之前，`executors._artifact_supersede.collect_superseded_tx`
   用 `select storage_key ... for update` 取同 `(job_id, node_key, name)` 行当前指向的
   key（不同于新 key 才记录）。lease 臂在 job-mutation 锁与代次复查之后执行；
   `for update` 让并发的同行登记排队，后到者读到先提交者的 key。
2. **登记提交后**，`services.job_artifact_versions.discard_superseded_objects` 走既有
   `delete_objects_guarded`：取 `artifact-authority:<key>` try-lock、锁内按本 job
   清单复核——仍被任一行引用（#853 前跨节点同名产物共用的 legacy key）则跳过，锁被
   在途 promote 持有则跳过；否则删除。永不抛出（登记已提交）。

结果：被取代对象删除后旧 URL 返回 404；删除失败或被跳过时，残留对象只承载旧字节
（旧 URL 返回旧字节），两种结局都满足验收。

rerun / run-to / upgrade 的清单行删除与对象清理（`rerun_artifact_cleanup`）、
job 删除（`delete_objects`）均按行 `storage_key` 工作，不需要改动；rerun 清理里
「重跑尝试在探针与删除之间重新登记同一稳定 key」的竞态，对版本 key 在构造上不再
成立（新尝试永远登记新 key）。

### 4.4 存量与兼容

- **无迁移**：存量行保持固定 key，读路径一律按行 `storage_key` 读。#853 后没有任何
  写入会再写固定 key，所以对存量固定 key 已签发的 URL 只会返回旧字节，或在该行被
  取代 / rerun 清理 / job 删除后 404。
- **Worker**：协议不变（staging 布局、claim 注入的 presigned PUT 不变），新旧
  Worker 混跑无影响。
- **回滚 Host 版本**：旧版本 Host 按行 `storage_key` 读，能读版本 key 行；它的写入
  回到固定 key，不影响已登记的版本 key 行。
- **raw 端点**：按名字选最新行（`lookup`）再读该行 key，「当前产物」语义不变。

## 5. 残余面

| 残余 | 结局 | 兜底 |
| --- | --- | --- |
| 并发**首次**登记同一 `(job, node, name)`（无行可 `for update`）| 先提交者的版本 key 被后提交者改指后无行引用（孤儿）；其间若已签发 URL，该 URL 只返回先提交者自己的字节 | GC / lifecycle；验收不受影响 |
| 被取代对象删除失败 / 锁被在途 promote 持有 | 旧 key 残留，只承载旧字节 | GC / lifecycle；验收不受影响 |
| 闸拒 / 中途失败的 promote | 新版本 key 孤儿（无行、未签发） | GC / lifecycle |
| 被取代对象删除前的进行中读取 | 旧 URL / raw 读到中途的对象被删，连接中断或 404 | 调用方重取清单（[workspace-api-tokens.md「读取产物」](../workspace-api-tokens.md#读取产物清单直连下载与全链路示例)） |

## 6. Quality Impact

- 正确性：直连 URL 的字节对应关系从「名字下的当前字节」收紧为「签发时那个版本的
  字节或 404」，消除 TTL 内重跑导致的静默错配；吊销残留窗口不再包含重跑新字节。
- 架构：写面注册表（EXEC-GENERATION-002）不变；新增两个小模块（执行层的被取代
  key 探针，服务层的版本 key 派生与提交后清理），SQL 留在执行层（BOUNDARY-DATA-001）。
  promote primitive 的覆盖契约测试经 `tests/fakes/artifact_keys.pin_legacy_authority_keys`
  把 key 钉回固定布局继续覆盖。
- 性能：每次登记多一次按主键的 `select ... for update`；发生取代时提交后多一次
  try-lock 事务 + 清单探针 + `DeleteObject`。版本 key 让 promote 的备份 `HEAD` 恒为
  miss、不再产生备份 copy，Worker 回传重跑少一次服务端 copy。
- 测试：`tests/services/test_artifact_direct_url_pinning.py`（验收主案、清理失败、
  lease 臂、Worker promote + gzip 形态、存量 key 退役、共用 legacy key 保留）；
  涉及固定 key 字面量的既有用例改为按清单行取 key。
