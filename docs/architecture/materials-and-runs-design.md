# 材料（Materials）与运行（Runs）：输入模型重设计

状态：v1 已实施（2026-08-22，worktree `.worktrees/materials-runs`，分支
`feat/materials-and-runs`，schema v53 + materials/runs API + 物化缓存 +
添加条目面板 + demo seed 迁移，quick gate 全绿）；v1.1/v2/future 待实施。
日期：2026-08-22
关联：Issue #141（intake 仅支持 question/video 实体）、
Issue #120（打包重设计：打包清单 + 批次追踪，本文档 §10 的衔接方）、
SECURITY-EXTERNAL-CONNECTION-001、CONFIG-MANIFEST-001、EXEC-CODE-003、
`node-sdk-and-worker-execution-design.md`

## 1. 背景与问题

当前 job 创建以 intake 为中心：实体类型限定 `question` / `video`，解析模式
（direct_ids / batch_by_urls / by_knowledge 等）围绕题库与视频源，用户必须先理解
source_kind / intake mode 才能提交任务。这带来三个问题：

1. **开放输入场景无法声明**：典型诉求是「给一堆材料（文档/图片/音视频混合），
   workflow 逐个处理并输出结构化结果」（Issue #141）。现有 intake 没有
   文件/文件夹实体，也没有通用引用实体。
2. **用户概念负担**：intake、source_kind、batch 都是内部机制泄漏到用户面的
   概念。新用户的心智只有一句「我给一批条目，每个条目给我一份结果」。
3. **产物体积失控**：历史 job 产物全量保留，packages 为全量 zip 拷贝且
   无过期机制（prod 单 workspace 打包快照超 6G）。打包整体重设计见
   Issue #120，本文档只负责把存储底座统一（§6.5），不在此解决。

## 2. 设计决策总览

| # | 决策 | 结论 |
|---|---|---|
| D1 | 主场景 | 浏览器上传（场景 B）；本地路径/挂载卷引用为后续增强 |
| D2 | 材料存储 | S3 兼容对象存储，默认部署 RustFS；代码只对 S3 API 编程 |
| D3 | 材料地位 | 一等资源：内容 hash dedup、归属 workspace、大小可计、TTL 可配 |
| D4 | job 输入抽象 | 条目（item）= 材料（material）\| 外部引用（ref）；一条目一 job |
| D5 | 解析时机 | 永远是节点执行时（pull 模型）；提交时不调任何外部接口 |
| D6 | intake 概念 | 用户面彻底消失；内部管线退化为「从条目创建 run」 |
| D7 | batch → run | batch 升级为一等 run 概念；配置冻结下沉到 job |
| D8 | 历史数据 | 路线 A：schema 迁移 `job_batches` → `runs`，保留全部历史 |
| D9 | by_knowledge | 退役；只接受精确 ID 列表，一条目一 job 一实体成为不变量 |
| D10 | connector | = external_connection（配置）+ 首节点拉取代码（逻辑），不新增实体类型；连接声明方向（source/sink），用户不选 connector，由 workflow 绑定 |
| D11 | 产物治理 | 不在本文档范围：打包整体重设计见 Issue #120 |
| D12 | 产物存储 | job 产物与材料统一走对象存储；job_dir 仅为执行暂存与本地缓存 |
| D13 | 存储路径 | 无 filesystem fallback，只走 S3 API；开发机共享一个 RustFS，按 worktree 派生 bucket |
| D14 | demo seed | 示例 workflow 同步迁移：示例材料随 demo workspace 播种，example_intake 改读材料输入 |

## 3. 用户场景与动线

### 3.1 场景分层（按「文件相对服务进程的位置」）

- **场景 A：本地自用**——整机部署，材料在本机磁盘。v1.1 支持：路径输入，
  服务端校验后拷贝/硬链进材料区，后续与场景 B 完全一致。
- **场景 B：浏览器上传（主场景，v1）**——用户访问部署实例，拖拽文件夹上传，
  浏览器经 presigned URL 直传对象存储，不经过后端进程。
- **场景 C：服务器侧素材库（NAS/挂载卷）**——v2 支持：白名单根目录 +
  原地引用（动态 `--allow-read`），避免拷贝大文件。

### 3.2 新用户动线（v1 目标态）

1. 进入 workspace → 「添加条目」：上传文件/文件夹，或粘贴文本（每行一个
   ID/URL）。**用户不选择 connector**——workflow 的输入契约已绑定该 workflow
   接受的连接（§7），粘贴的 ID 按绑定的 connector 解析。
2. 提交前预览：共 N 个条目（12 个文件、8 条引用），按类型分组、总大小、
   当前 workflow 支持/不支持的类型标注。
3. 确认 → 创建 run → 一条目一 job 铺开，job 标题 = 文件名/原始 ID。
4. 节点逐个处理：file 条目从本地材料缓存读文件；ref 条目经 connector
   拉取内容。结果写 `result.json` / `report.md`。
5. workspace 层面查看进度、逐 job 看结果、自选 job 打包下载。

全程不出现 intake / source_kind / batch 字眼。用户可见的概念只有：
**条目、材料、运行、任务**。

## 4. 核心概念模型

```
条目（item）                     job 的唯一输入，一条目一 job
 ├─ file 材料（material）        字节存对象存储，内容寻址，一等资源
 └─ 外部引用（ref）              {connection_key, external_id, params?}
                                  无字节，执行时经 connector 拉取

运行（run）                      一批条目 × 一个 workflow 的一次执行
                                  承载：frozen pins、暂停、统计、quality replay

任务（job）                      run 内单条目的执行单元，携带自身输入与冻结配置
```

不变量：

- **一条目一 job，一 job 一实体**（D9）。by_knowledge 式「一个输入展开多个
  实体」退役后，结果展示、打包、质量标注的粒度假设统一。
- **提交即同步返回**（D5）。创建 run 只做：格式校验、拆行、内容 hash dedup、
  建 runs + jobs。不调外部接口，不解析内容。
- **解析永远在执行时**。ref 的内容拉取、file 的物化都发生在 dispatch/节点
  执行阶段。提交时的「标题展示」只是 best-effort 后台 enrichment（拉不到
  就显示原始 ID），不是架构路径。

## 5. 数据模型

### 5.1 `materials` 表（新增）

```
id                uuid / text 主键
workspace_id      归属 workspace
content_hash      sha256，(workspace_id, content_hash) 唯一 → 上传即 dedup
filename          原始文件名（展示用）
content_type      MIME
size_bytes
storage_key       对象存储 key（bucket 由实例配置决定，见 §6.3）
status            uploading | ready | failed | expired
created_by / created_at / expires_at
```

### 5.2 `runs` 表（由 `job_batches` 迁移而来，路线 A）

```
id                新格式 run id；历史行保留原 batch id 不变
workspace_id
workflow_key
source_kind       legacy 展示用（退役 intake 后逐步废弃）
status            created | queued | running | paused | finished（汇总）
frozen_pins_json  node_code_versions / agent_versions / quality_replay 标记
                  （从旧 source_payload_json 解析搬迁；新行直接写列）
stats_json        状态汇总缓存（total/succeeded/failed）
queue_payload_json 异步 intake 的工作状态（chunk 消费/重排队），
                  由旧 payload 的 _intake_queue 块保留而来
created_count / error_message  异步建 job 的进度与错误（进度面板的
                  数据基础，见 §11 future 行）
created_by / created_at / updated_at
```

### 5.3 `jobs` 表变更

```
input_json        {type:"material", material_id} | {type:"ref", connection_key,
                  external_id, params?}——job 输入的一等字段，替代 batch payload
                  里的 task_candidates 留痕
frozen_config_json 冻结的节点配置（从 batch payload node_config 下沉）
run_id            由 batch_id 改名，值不变
```

`jobs.source_type/source_id` 保留作展示与兼容（material → filename /
ref → external_id），不再承担输入寻址职责。

### 5.4 退役

- `RESOLVERS` 注册表（`job_intake_registry.py`）整体删除；
  `phase`（intake/node/direct）三态、source_kind、workflow 定义的
  `intake.modes` 声明随之退役。
- `JobIntakeService` 重构为 `RunService`（校验 + 建 run + 建 jobs），
  异步队列（queued batch 消费）语义平移到 run。
- workflow 定义改声明**输入契约**：接受的条目类型（file/ref）、文件类型
  白名单、ref 绑定的 connection key（固定值，非用户选择，见 §7 的方向
  约束）。

## 6. 存储层

### 6.1 为什么 S3 兼容（默认 RustFS）

- **上传不过后端**：presigned URL 浏览器直传，FastAPI 只发签名、记元数据。
- **remote worker 天然可用**：worker 有网络，直接从对象存储拉材料——
  「动态 allow-read / Host-only 节点」的妥协方案不需要存在。
- **retention 原生抓手**：bucket lifecycle 规则即材料 TTL。
- **凭据走实例级 infra 配置**：材料存储是平台基础设施（与数据库同级），
  不是业务 connector——endpoint/bucket/密钥按 `database.url` 同一模式
  env-only 注入（`AGENT_LEGION_S3_*`，密钥值不落 tracked yaml / 日志），
  开发环境由 `init-worktree.sh` 写进 worktree `.env`；`deploy/` 加一个
  compose 服务（RustFS，Apache 2.0）。业务 connector 的凭据仍走
  `external_connections` + 实例 vault（SECURITY-EXTERNAL-CONNECTION-001），
  两者不混。

### 6.2 节点访问：物化 + 内容寻址缓存

沙箱边界不变（节点默认无网络，EXEC-CODE-003 fail-closed）：

```
dispatch → 按 material content_hash 查本地 materials_cache/
           未命中则从 S3 流式下载（复用 workspace_libs/download.py）
           → 缓存目录静态进 --allow-read → 节点读本地只读文件
```

同一材料多 job 复用不重复下载；Host 与 Worker 行为一致。缓存本身有
LRU/容量上限（实例设置可配），淘汰不影响正确性（下次重新下载）。

### 6.3 开发与测试环境（无 filesystem fallback，D13）

**决策：不做 fs fallback**，代码只有一条存储路径（S3 API），避免双路径的
长期维护与行为漂移。RustFS 是自托管服务（单 Rust 二进制 / 单 Docker
镜像，Apache 2.0，资源占用与 MinIO 同级），开发环境成本可控：

- 每台开发机跑**一个共享 RustFS 实例**（compose 或裸二进制），按
  worktree 名派生独立 bucket——与 per-worktree Postgres 库同一模式；
  `scripts/init-worktree.sh` 负责建 bucket，并把 endpoint / bucket /
  凭据写进 worktree 的 `.env`（同 `AGENT_LEGION_DATABASE_URL` 派生）。
- 测试不碰真实 S3：单元/集成测试在存储客户端接口上打 test double；
  CI 需要端到端时跑一个 rustfs service 容器。

### 6.4 上传协议

```
POST /api/workspaces/{id}/materials/presign   {filename, size_bytes, content_hash?}
   → {material_id, upload_url}                  已存在同 hash 直接返回 ready
PUT  upload_url（浏览器 → S3 直传；v1 为单 PUT presign，大文件 multipart
     与签名级 size 约束（presigned POST policy）列为后续优化）
POST /api/workspaces/{id}/materials/{mid}/complete  服务端校验 size/hash → ready
```

SigV4 presigned PUT 无法约束 Content-Length，size/hash 一律由 complete 时
服务端 HEAD + 流式校验强制（v1 实现为完整读自算 sha256，简单可靠；上传端
带 checksum 头后可免读）。`(workspace_id, content_hash)` 的 dedup 唯一性
以部分唯一索引实现（`content_hash` 可选，空串不参与唯一）。

文件夹上传用 `webkitdirectory` 递归读，逐文件走同一协议，客户端并发控制。

### 6.5 job 产物统一进对象存储（D12）

材料与产物是同一种东西的两端——都是「workflow 要读写的字节」。统一存储后：

- **job_dir 降级为执行暂存**：沙箱 `--cwd` 语义不变，节点照常写 job_dir；
  节点/job 完成时由执行面（Host 或 Worker）把产物上传对象存储
  （key 前缀按 `workspace/job/` 组织）。本地 job_dir 成为可淘汰缓存，
  体积治理从「给文件系统打补丁」变成「给缓存配容量」。
- **remote worker 的产物回传与材料下发走同一条 S3 通道**，不再有
  「产物怎么从 worker 回 host」的独立协议。
- 产物进对象存储后，打包重设计（#120，§10）的「选择性打包部分
  artifacts」就是「按清单从 bucket 取文件生成 zip」，天然成立。
- 迁移期兼容：老 job 的产物仍在本地 job_dir，读路径按「对象存储优先、
  本地回退」解析，不搬历史数据。

### 6.6 可平行迁移到 Amazon S3

代码只对 S3 API 编程（presign、GET/PUT、multipart、lifecycle），不依赖
任何 RustFS 特性。因此部署后端可以平行替换：自研/私有化用 RustFS，
上云直接切 Amazon S3（或 MinIO/Garage），只改 external_connections 的
endpoint 与凭据配置，零代码变更。数据搬迁用 bucket 间同步工具
（`aws s3 sync` / `rclone`）即可，存储 key 布局与后端无关。

## 7. Connector 模型

connector 拆成两部分，各有现成归宿，**不新增实体类型**：

- **连接配置** = `external_connections` 条目（endpoint + 凭据入实例 vault，
  admin UI 维护）。ref 的 `connection_key` 直接引用。
- **解析逻辑** = workflow 首节点的节点代码：经 `ctx.service_config(key)`
  拿配置、`workspace_libs/http_client.py` 发请求。维护方式 = node_code
  现有发布流（DB 发布文本、版本不可变、draft→published）。

**方向约束（防误选）**：连接声明方向——`source`（拉取输入，如 CMS 题库）
或 `sink`（上传产出，如业务库回传，见 #120）。prod 上两类连接并存的
情况下，让用户从下拉框选 connector 必然误选，因此：

- workflow 的输入契约**固定绑定**一个 source 连接的 key（§5.4），用户
  粘贴 ID 时无需也不可选择；
- 「添加条目」UI 只展示条目内容（ID 列表），connector 是 workflow 的
  实现细节，不出现在用户面；
- sink 连接只出现在 workflow 的出站节点配置里（workflow 作者视角），
  与任务提交者无关；
- `external_connections` 增加 `direction` 列，服务端校验：ref 引用的
  连接必须是 source，出站节点引用的必须是 sink。存量连接迁移时按现有
  用途一次性标注。

跨 workflow 复用痛点出现时，演进为 `versioned_entities` 第三种实体类型
`connector`（workspace 作用域、发布生命周期一致、dispatch 注入沙箱依赖、
worker bundle 与 import 闭包扫描相应扩展）。用户面 ref 模型不变，无数据
迁移。列为 future work，等第二个真实复用者出现再做。

## 8. 迁移计划（路线 A）

一个 schema 版本（vN+1）内完成：

1. `job_batches` → `runs`：建 runs 表，旧行原样搬入（id 不变）；
   `source_payload_json` 解析出 `node_code_versions` / `agent_versions` /
   `quality_replay` 填入 `frozen_pins_json`；`task_candidates` /
   `node_config` 解析后下沉到各 job 的 `input_json` / `frozen_config_json`
   （旧候选无材料概念，`input_json` 落成 `{type:"ref", ...}` 等价形态或
   legacy 标记）。
2. `jobs.batch_id` → `jobs.run_id`（列改名，值不变）。
3. quality 抽样/标注/统计、batch 暂停（`jobBatchPauseApi`）、worker 协议里
   的 batch 引用全部改指 runs。
4. `JobIntakeService` → `RunService`；`RESOLVERS`、intake modes 校验、
   phase 分派删除。
5. API 兼容：`POST /job-batches` 保留一个版本的 shim（内部转 RunService），
   标注 deprecated；前端直接切新 API。

迁移测试：构造含各种旧 payload 形态（direct/opaque/quality_replay）的
fixture，迁移后 replay、暂停、统计行为等价。

## 9. Demo workspace seed（D14）

现状：示例 DAG（`server/app/workflows/builtin.py`）的两个节点经
`demo_node_seed.py` 发布为 global node_code；`example_intake.py` 按
`knowledge_dir` 配置读 `examples/` 下的演示 JSON——这条路能走通只是因为
Host 沙箱 allow-read 碰巧含 `examples/`（Worker 上根本不存在该目录）。
输入模型切换后必须同步迁移：

- 示例 workflow 的 `intake.modes` 声明删除，改为输入契约（接受 file/ref
  条目）；
- `example_intake.py` 从「读 `examples/` 目录」改为「读 job 的 material
  输入」（经 §6.2 的物化缓存路径）；
- seed 时把 `examples/` 的演示文件（小 JSON）作为**示例材料**播种进 demo
  workspace 的材料区（seed-if-absent，与 node_code 种子同一模式）——
  新用户创建 demo workspace 后零准备即可点击运行，恰好演示目标体验；
- `tests/helpers.seed_workspace_agent_definitions` 及 demo 相关 fixture
  同步；`agent_catalog_builtin`（Agent 模板种子）不受影响；
- 硬化收尾：demo 不再读 `examples/` 后，评估把 `examples/` 移出
  `_REPO_READ_SUBDIRS`（`_code_sandbox.py`），收缩沙箱读面。

## 10. 产物与打包：移出本文档范围（见 Issue #120）

打包需要整体重设计，不在本版本解决。用户对完成 job 的诉求是「这批内容
拿去用」，形态有两种，都由 #120 覆盖：

1. **出站 connector 回传**：workflow 内增加 sink 方向的出站节点，把结果
   直接上传到用户自己的业务库；
2. **选择性打包**：job 产出含大量中间 artifacts，只有部分文件需要进包——
   #120 的 workspace 级打包清单 + 打包批次追踪解决选择性与可追溯。

本文档与 #120 的衔接点：

- 产物统一进对象存储（§6.5）后，选择性打包 = 按清单从 bucket 取文件
  生成 zip，出站回传 = sink 节点直读产物 key，两者都不依赖本地 job_dir；
- 材料侧 TTL 仍属本文档范围：bucket lifecycle + `materials.expires_at`，
  引用计数为 0 才物理删除；
- job_dir 作为可淘汰缓存配容量上限即可，历史 job 产物全量保留的问题
  随产物上云自然消解；prod 存量 6G 在 #120 落地前手动清理。

## 11. 分阶段实施

| 阶段 | 内容 | 用户可见变化 |
|---|---|---|
| v1 | materials 表 + S3 存储 + presigned 上传 + 物化缓存；ref 条目；run 概念与路线 A 迁移；「添加条目」面板；demo seed 迁移（§9） | 上传/粘贴 ID 提交任务，intake 消失 |
| v1.1 | 场景 A 路径输入（拷贝进材料区） | 本地路径提交 |
| v2 | workflow 输入契约声明；question/video 导入改造为 connector 形态；场景 C 原地引用 | workflow 声明更直白 |
| 并行 | 产物上云后的打包重设计（Issue #120） | prod 体积受控、出站回传 |
| future | connector 实体化；文件夹作为单 job 输入（manifest 条目） | — |
| future（已立项，方案待讨论） | **异步建 job 的进度与结果可见性**：万级 job 走异步队列创建时，界面只看到数量上涨，看不到创建进度与结果分布（成功 / 因重复被 dedup / 校验失败及原因）。需求：run 维度展示创建进度条与结果明细。具体方案另行讨论后补本节 | — |

## 12. Quality Impact

- **新增 invariant 候选**（实施时同步 `config/architecture/`）：
  - INTAKE-RETIRE-001：job 创建只经 RunService，禁止新增 source_kind /
    resolver 形态；
  - RUN-FREEZE-001：冻结配置与 pins 以 job/run 列为权威，禁止回读
    batch payload 形态；
  - MATERIAL-ACCESS-001：节点访问材料只经物化缓存静态 allow-read，
    禁止动态放行任意宿主路径；
  - MATERIAL-SECRET-001：材料存储凭据只走 env-only infra 注入（同
    `database.url`），不落 tracked yaml / API / 日志。
  - CONNECT-DIRECTION-001：external_connections 必须声明方向；ref 只许
    引用 source 连接，出站节点只许引用 sink 连接，服务端强校验。
- **测试**：新测试放 `tests/services/`（RunService、材料 dedup、迁移）、
  `tests/routes/jobs/`（条目 API、上传协议）、`tests/executors/`（物化
  缓存与沙箱 allow-read）；不新增 `tests/` 根文件。路线 A 迁移测试用
  旧 payload fixture 做行为等价断言。
- **安全面**：presigned URL 限 workspace 作用域与过期时间；complete 时
  服务端校验 size/hash 防声明与实际不符；ref 的 connection_key 校验
  workspace 可见性；材料下载走 `download.py` 的 SSRF 守卫。
- **风险**：RustFS 项目较年轻 → 代码只对 S3 API 编程，可换 MinIO/Garage/
  AWS S3；迁移涉及全量历史行 → 迁移脚本必须幂等、可重入，先在 prod
  副本上演练。
