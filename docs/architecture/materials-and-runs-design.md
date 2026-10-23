# 材料（Materials）与运行（Runs）：输入模型重设计

> **历史设计记录**（#1103）：现行参考（概念模型、入口契约、数据模型、存储层、残余面）已抽出到
> [materials-and-runs.md](materials-and-runs.md)；本文保留决策表、场景、迁移计划与分阶段实施记录，
> 供溯源。

状态：设计稿（实施进度以 §11 为准）。v1 已实施（schema v53 + materials/runs API +
物化缓存 + 添加条目面板 + demo seed 迁移）；v1.2（bundle 文件夹条目，#156/#164）
已落地（schema v55）；v1.3（text 条目「输入需求」，#761）与 v1.4（start 节点
`text_input` 呈现配置，#764）已随 0.7.14 落地；v1.1/v2/future 待实施。
本文是设计记录：现行行为以代码与 `config/architecture/architecture-invariants.yaml`
（MATERIAL-* / RUN-FREEZE-001 等）为准。
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
   无过期机制（单 workspace 的打包快照可膨胀至数 GB）。打包整体重设计见
   Issue #120，本文档只负责把存储底座统一（§6.5），不在此解决。

## 2. 设计决策总览

| # | 决策 | 结论 |
|---|---|---|
| D1 | 主场景 | 浏览器上传（场景 B）；本地路径/挂载卷引用为后续增强 |
| D2 | 材料存储 | S3 兼容对象存储，默认部署 SeaweedFS（#340；RustFS 为存量逃生舱）；代码只对 S3 API 编程 |
| D3 | 材料地位 | 一等资源：内容 hash dedup、归属 workspace、大小可计、TTL 可配 |
| D4 | job 输入抽象 | 条目（item）= 材料（material）\| 外部引用（ref）；一条目一 job |
| D5 | 解析时机 | 永远是节点执行时（pull 模型）；提交时不调任何外部接口 |
| D6 | intake 概念 | 用户面彻底消失；内部管线退化为「从条目创建 run」 |
| D7 | batch → run | batch 升级为一等 run 概念；配置冻结下沉到 job |
| D8 | 历史数据 | 路线 A：schema 迁移 `job_batches` → `runs`，保留全部历史 |
| D9 | by_knowledge | 退役；只接受精确 ID 列表，一条目一 job 一实体成为不变量 |
| D10 | connector | = external_connection（配置）+ 首节点拉取代码（逻辑），不新增实体类型；连接声明方向（source/sink），用户不选 connector，由 workflow 绑定 |
| D11 | 产物治理 | 不在本文档范围：打包整体重设计见 Issue #120 |
| D12 | 产物存储 | job 产物与材料统一走对象存储；job_dir 仅为执行暂存与本地缓存（已落地，#160 / schema v54，§6.5） |
| D13 | 存储路径 | 无 filesystem fallback，只走 S3 API；开发机共享一个本地对象存储，按 worktree 派生 bucket |
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
 ├─ 材料（material）             字节存对象存储，内容寻址，一等资源
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
- **解析永远在执行时**。ref 的内容拉取、material 的物化都发生在 dispatch/节点
  执行阶段。提交时的「标题展示」只是 best-effort 后台 enrichment（拉不到
  就显示原始 ID），不是架构路径。

### 4.1 入口契约：start 节点与 accepted_item_types（EXEC-WORKFLOW-START-001）

Workflow 定义必须恰好包含一个 `type: start` 节点，它是条目类型契约的结构化
家（Dify 式画布内 Start 节点）：

```yaml
nodes:
  _start:
    type: start                      # 豁免 capability；不得声明 execution/shard/reduce/terminal/config/config_schema
    accepted_item_types: [material]  # 可选；缺省 [material, ref]（向后兼容）
```

- **边约束**：start 不得有入边、必须至少有一条出边、出边不得带
  condition——start 永不执行，`when` 引用的产物永远不会存在，条件出边
  只会让整支静默 not_applicable，loader 直接 fail-fast。
- **不执行**：start 永不进入 `job_nodes`，调度器（`find_ready_nodes` /
  `evaluate_branches`）把它视为恒 completed，出边天然满足，首节点立即 ready；
  `allowed_nodes`、publish 校验（code 可解析性 / Agent 唯一性）、节点配置解析
  一律跳过它。
- **不可删**：loader 对没有 start 的存量定义（旧 revision、进行中 job 的快照）
  自动注入合成 start——缺省全接受、出边指向所有无入边节点（旧语义的隐式根），
  parse→serialize 对新旧定义对称，hash 与结构比较不受影响，零迁移。
- **创建期校验**：`RunService.create_run` 在任何写库之前按 start 的
  `accepted_item_types` 拒绝未接受的条目类型（400）；前端 AddItemsDialog 按
  active revision 的同一契约禁用对应 Tab。legacy `intake.modes` 与 start 正交，
  只约束 `/job-batches` 旧路径直至退役。
- **v1 `edges:` 键**：无 `schema_version` 的 v1 定义里手写的 `edges:` 键
  原为静默忽略；现改为解析合并——`after` 派生边优先、重复边（同
  source/target）去重且 condition 以派生边为准（重复显式边上的 condition
  静默丢弃）。这是快照重载对称的前提：快照携带的物化边（含注入的 start
  出边）能与派生边集正确合并。
- **`text` 条目（直接输入需求）**：`{type:"text", content, filename?}` 把需求
  文字直接写进 run 请求；`RunService` 在契约/节点配置/pin 全部校验通过后、
  run 行写入前，把它落成一份 ready 材料（sha256 内容寻址，经服务端材料写入 chokepoint
`material_object_publish`：先落无 hash 的 uploading intent 行、再写对象、再原子换成 ready
行——行先于对象，#1314，见 §6.4.2；
  `.md`/`.txt`/`.json` 文件名白名单（`.json` 落盘 `application/json`，#813），UTF-8 ≤ 64 KiB，`run_text_items.py` +
  `jobs/queries/material_inline.py`），再改写成普通 `material` 条目进入解析——
  `input_json`、manifest、Worker 物化、skill 看到的与手动上传同名文件完全
  一样。这是 run 创建前唯一的写：材料是 workspace 资产（无引用时 TTL 回收），
  与「先上传再被拒」留下的状态等价，fail-closed 契约不变；对象存储未配置
  时整条请求 503。同一文本重复提交命中同一材料、同一 job dedup 键。契约
  缺省不含 `text`（存量 fail-closed），Studio 入口节点显式勾选「直接输入
  需求」后「添加条目」出现「输入需求」Tab。
  多条文本先全部校验、预检 hash，再整批落 intent 行并暂存对象，最后以单个事务把
  intent 行换成 ready 行；中途失败回滚整批新行，并补偿删除本次的 intent 行与暂存对象
  （先删行、后删对象）。文本入口从不复活、重定向或修改既有材料行。
  对象 key 包含请求独占随机段，hash 仍是材料去重身份；并发冲突只复用 ready
  胜者材料、删除落败对象，不能按共享
  hash 路径补偿删除。提交和清理核验使用同一 workspace 事务锁，避免提交
  确认丢失时误删已提交对象。清理时数据库或存储不可达会记录待清理 key，
  保留对象以保护数据（intent 行随之保留，对象不会被 GC 删除）；进程崩溃留下的 uploading
  intent 行可在材料列表删除，对象删除失败留下的单次使用 key 孤儿由 GC 材料删除批回收。
  文件名拒绝控制字符，内容和文件名拒绝无效 UTF-8，避免写入后才发生编码错误。
  混合提交含超长需求时禁用创建按钮（包括切换到其他 Tab 后），避免悄悄
  丢掉需求、只提交其他材料。

  **状态与所有权（MATERIAL-INLINE-OWNERSHIP-001）**：`created_at` 与状态
  都不能证明最后一次 presign URL 已失效。`failed` 可能仍有在途 PUT，
  `expired` 由 TTL 协议管理；文本入口不能凭年龄或非 ready 状态接管它们。

  | 事务内当前状态 | 文本提交行为 |
  | --- | --- |
  | hash 不存在 | 插入本次暂存对象对应的 ready 材料 |
  | 并发胜者为 ready | 复用其身份与元数据，清理本次落败对象 |
  | uploading / failed / expired | 整批返回 409，保留原行与原对象 |
  | 预检查选中的 ready 行已删除、换身份或变为非 ready | 整批返回 409，不复用旧快照 |

  唯一索引裁决新 hash 的所有者，`ON CONFLICT DO NOTHING` 后以 `FOR UPDATE`
  读取当前行；预检查命中的 ready 行也在同一事务内重验 ID 和状态。普通
  presign/complete 无需加入文本专用 advisory lock：文本入口从不改写它们
  的状态和 storage_key。浏览器迟到 PUT 仍指向原材料，complete 仍验证其
  大小与 hash。用户先完成/重试原上传；确认放弃后可通过材料 API 显式删除
  旧材料，expired 材料仍走既有 TTL 清理，然后再提交文本。

  **Quality Impact**：无 DDL；批量事务和对象引用查询经 `JobQueries` 门面，
  保持 BOUNDARY-DATA-001；回归覆盖第二次 PUT/行写入失败、同名/异名并发、
  反序批次、提交确认丢失、补偿不误删胜者对象，以及 presign/text 双向竞争、
  迟到 PUT/complete、旧非 ready 行、TTL/删除/换身份后的 ready 快照失效。
- **`text_input` 呈现配置（start 节点可选块，`start_text_input.py`）**：
  `{label, filename, template}` 分别配置输入框标题、默认文件名和预填模板。
  显式条目文件名优先，未提供时依次回落配置与 `需求.md`；仍使用上述批次
  事务、状态重验和请求独占对象补偿，不改变上传所有权。
  loader 校验字符串、长度与裸 `.md`/`.txt`/`.json` 文件名（后缀与运行时白名单同源 `text_item_content_types.py`），全空块归一为未声明；
  不勾选 `text` 时配置惰性，echo / 快照对称，compare 记 info 级变更。
  前端未修改的模板不计条目，可一键恢复；超长文本阻止整次混合提交。
  草稿 YAML 是不可信运行时数据：`text_input` 在整图与 ghost 节点进入
  `WorkflowNodeRecord` 前共用形状解析，区分未声明与非法值，不把数字、
  数组或对象强转为文本。非法草稿沿现有 published 回退路径展示；编辑器
  提示先修复 YAML，阻止字段编辑覆盖原始非法内容；合法字段编辑只读取
  当前草稿，不从 published 回退记录重建其他字段。契约编辑复用结构准入，
  取消 text 可显式删除非法配置块。Quality Impact：形状
  反例同时覆盖两条记录转换和编辑器回退/恢复，服务端发布校验仍为权威。
- 后续切片（folder 整体式/bundle 等新条目类型）的配置也挂在 start 节点上——
  这正是 start 存在的意义。

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
frozen_pins_json  node_code_versions / agent_versions / node_profiles（#1079 回放执行档案）/ quality_replay 标记
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
                  external_id, params?} | {type:"bundle", bundle_id}——job 输入的
                  一等字段，替代 batch payload 里的 task_candidates 留痕
frozen_config_json 冻结的节点配置（从 batch payload node_config 下沉）
run_id            由 batch_id 改名，值不变
```

`jobs.source_type/source_id` 保留作展示与兼容（material → filename /
ref → `connection_key:external_id`，身份按连接限定：同一 external_id 跨
连接是不同条目，去重键与 job id 同样按此派生），不再承担输入寻址职责。

**条目级 `client_token`（#813）.** 条目身份默认纯内容寻址（material /
bundle id、ref 的 `connection_key:external_id`；text 规范化为内容寻址
material），同一内容永远命中同一 job。material / bundle / text 条目可选带
`client_token`（1–64 字符 `[A-Za-z0-9._-]`，首字符为字母或数字），候选
身份变为 `source_id = <id>~<client_token>`：去重键与 job id 随之派生，
同内容不同 token 各成独立 job、同 token 重提幂等命中同一 job；run digest
对提交的 items 逐字哈希，token 自然入摘要（不同 token 的 run 不互相治愈）。
text 条目的 token 在规范化前捕获并带到改写后的 material 条目上（同一材料、
不同 job）。`input_json` 不携带 token，下游与无 token 的同内容 job 完全
一致。不带 token 的条目 `source_id`、job id、run id 与此前逐字节相同（存量
不漂移，`tests/routes/test_runs_client_token_api.py` 钉住）。ref 条目不收
token（契约 422）：`external_id` 已是调用方控制的命名空间，且其自由文本会与
`~` 分隔符产生歧义。实现：`services/run_item_client_token.py`。

### 5.4 `material_bundles` 表（bundle 条目，#156）

文件夹作为**一个整体条目**创建一个 job：成员文件走 §6.4 常规上传协议，
全部 ready 后一次创建调用冻结 manifest（引用式，不复制字节）。

```
material_bundles
  id / workspace_id / name（文件夹根名）
  total_size_bytes / file_count（创建时汇总快照）
  created_by / created_at
material_bundle_members
  bundle_id → material_bundles.id（级联删）
  material_id → materials.id
  path      成员在 bundle 内的相对路径（剥公共前缀）
  ordinal   按 path 排序的稳定序号
```

- manifest **不可变**：创建后无更新路径，改成员 = 新建 bundle。
- 成员仍是普通材料，删除双向守卫：被任一 bundle 引用的 material 拒删；
  被任一 run 条目引用的 bundle 拒删。TTL collector 适用同一成员守卫：
  bundle 成员的 expired 行跳过物理回收，先删 bundle 才会放行。
- 成员相对路径拒绝控制字符（TAB/LF 等）：manifest 哈希按行拼接
  `member_address<TAB>path`，路径携带分隔符会让编码产生歧义。
- 条目/契约类型统一为字面量 `bundle`（item type、accepted_item_types、
  source_type）；start 节点 DEFAULT 契约保持 `("material","ref")`，存量
  workspace 对 bundle 条目 fail-closed，需显式 opt-in。
- 物化：dispatch 时成员先进内容寻址缓存，再按 manifest 组装**硬链接
  目录树**，地址由成员集合确定性派生（`shared/material_bundle.py`，
  Host/Worker 同一规则）；Worker 经 claim 注入的 presigned 通道逐个拉
  成员，与单材料同一协议（MATERIAL-BUNDLE-001）。缓存纪律两处为
  bundle 收紧：逐成员物化全程 pin 住全部成员与树根（成员 i+1 的淘汰
  回合不得删掉成员 i）；缓存淘汰按 entry 粒度——单文件或整棵目录树
  原子删除，绝不留下残缺树（「目录存在即完整」不变式因此成立）。

### 5.5 退役

- `RESOLVERS` 注册表（`job_intake_registry.py`）整体删除；
  `phase`（intake/node/direct）三态、source_kind、workflow 定义的
  `intake.modes` 声明随之退役。
- `JobIntakeService` 重构为 `RunService`（校验 + 建 run + 建 jobs），
  异步队列（queued batch 消费）语义平移到 run。
- workflow 定义改声明**输入契约**：接受的条目类型（material/ref）、文件类型
  白名单、ref 绑定的 connection key（固定值，非用户选择，见 §7 的方向
  约束）。

## 6. 存储层

### 6.1 为什么 S3 兼容（默认 SeaweedFS，#340；RustFS 为存量逃生舱）

- **上传不过后端**：presigned URL 浏览器直传，FastAPI 只发签名、记元数据。
- **remote worker 天然可用**：worker 有网络，直接从对象存储拉材料——
  「动态 allow-read / Host-only 节点」的妥协方案不需要存在。
- **retention 原生抓手**：bucket lifecycle 规则即材料 TTL。
- **凭据走实例级 infra 配置**：材料存储是平台基础设施（与数据库同级），
  不是业务 connector——endpoint/bucket/密钥按 `database.url` 同一模式
  env-only 注入（`AGENT_LEGION_S3_*`，密钥值不落 tracked yaml / 日志），
  开发环境由 `init-worktree.sh` 写进 worktree `.env`；`deploy/` 加一个
  compose 服务（SeaweedFS，Apache 2.0，#340 后默认；RustFS 为存量逃生舱）。
  业务 connector 的凭据仍走
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

bundle 条目（§5.4）复用同一缓存：成员按各自 content_hash 物化后，按
manifest 在缓存内组装硬链接目录树（地址由成员集合确定性派生，
Host/Worker 一致），节点看到的是一个只读文件夹。

### 6.3 开发与测试环境（无 filesystem fallback，D13）

**决策：不做 fs fallback**，代码只有一条存储路径（S3 API），避免双路径的
长期维护与行为漂移。本地对象存储是自托管服务（默认 SeaweedFS 单容器
all-in-one，#340；RustFS 为存量逃生舱），开发环境成本可控：

- 每台开发机跑**一个共享本地对象存储实例**（compose 或裸二进制），按
  worktree 名派生独立 bucket——与 per-worktree Postgres 库同一模式；
  `scripts/init-worktree.sh` 负责建 bucket，并把 endpoint / bucket /
  凭据写进 worktree 的 `.env`（同 `AGENT_LEGION_DATABASE_URL` 派生）。
- 测试不碰真实 S3：单元/集成测试在存储客户端接口上打 test double；
  CI 需要端到端时跑一个本地 S3 兼容 service 容器。

### 6.4 上传协议

```
POST /api/workspaces/{id}/materials/presign   {filename, size_bytes, content_hash?}
   → {material_id, upload_url}                  已存在同 hash 直接返回 ready
PUT  upload_url（浏览器 → S3 直传；v1 为单 PUT presign，大文件 multipart
     与签名级 size 约束（presigned POST policy）列为后续优化）
POST /api/workspaces/{id}/materials/{mid}/complete  服务端校验 size/hash → ready
```

`filename` 不得以 `/` 结尾、不得含空 / `.` / `..` 段、不得含反斜杠或控制字符（presign
422，#1119）：材料 key 为 `{workspace_id}/{hash}/{filename}`，以 `/` 结尾的 key 会被
SeaweedFS 存成目录（内容被丢弃），下载与备份落不了文件系统；空段与 `.` / `..` 段在下载时
被归一化吃掉（`a//b` 与 `a/b` 落同一文件互相覆盖）；含换行的 key 会让备份清单的逐行表达
判失败。纪律与产物名 `valid_artifact_name` 同源，放宽点只剩材料名允许中间的非空 `/` 段
（多段相对路径），另按 bundle 成员路径 `validate_member_path` 补齐控制字符禁令。
`content_hash` 非空时必须是 64 位小写 sha256 hex（422，#1267）——它同样拼进 key 且是
dedup 身份，不限制格式会把 `../other` 之类值原样签进 key。材料 key 统一带代次段与标记唯一段
`{workspace_id}/{hash}/v2g{代次}/m_{material_id}/{filename}`（首发即 v2g0，用户 filename
落在两段之后、不可伪造新代次命名空间；更早的存量行无代次段、key 不可变），每次重试（复位
uploading）把行迁到新代次 key 并把 `upload_generation` +1（schema v97，代次在行锁内原子
推进、key 由锁内实际新值派生）：此前签发的 PUT URL 全部指向死 key（上传能力随代次失效，
收紧前入库的非法 key 也随之不再签发），旧对象交 GC/bucket lifecycle 回收；complete 落
ready/failed 前在行锁内复查 verify 身份（storage_key + size_bytes + 代次）与当前状态，
不一致返回 409 由客户端重调 complete，并发 complete 已落 ready 时幂等不覆盖（#1267 codex
R2–R5 P1）。`v2g` 版本标记位于写端无法注入的第 3 段（#1267 起在役写端该段恒为服务端
派生的 `g<int>`，发不出 `v2g0`；codex r1/r2 P1 族——「第 4 段标记」两轮被「合法文件名
巧合」反例击穿后的构造性关闭），`m_{material_id}` 标记第 4 段行唯一段：key 跨行不可复用
——删行后同文件同名再上传必得新 key。S3 孤儿 GC 的材料删除批以它为准入门禁
（`s3_jobs_gc.material_key_single_use`），配合写端「行先于对象」协议与年龄下限重开删除
（#1314，§6.4.2）；inline 文本与 demo seed 两条服务端产线同文法。

ETag 内容身份（#1279）：complete 的 HEAD 核验同时取对象 ETag，与 ready 同事务落
`materials.object_etag`（inline/demo 产线在 put 后 HEAD 取 ETag 一并落列）——对无
content_hash 的行它是唯一的内容身份，关闭「已签发 URL 的 1h 窗口内同尺寸静默覆盖」残留
洞：消费两侧在 cache miss 时核验，Host 物化 GET 带 `If-Match`（S3 原生 412，原子绑定
所取字节），Worker 拉取对 presigned GET 带 `If-Match` 头（未签名请求头允许，claim 描述符
携带 `object_etag`，memory-only 不落 manifest），不一致即 MaterializeError 不落缓存；
cache hit 不重验（填充时已验）。complete 的 sha256 流读同样携带刚 HEAD 到的 ETag 作
If-Match（codex P1：HEAD 与读取之间对象被复用 URL 换掉时按核验失败处理，绝不把旧快照的
ETag 与新字节一起落 ready）。存量行 `object_etag=''`（v98 前完成，无内容身份）维持升级前
的 size-only 语义：各消费入口不核验、不挂起、不拒绝（#1312 方案 B）；一次性残留窗口与
逐入口语义见 §6.4.1，存量行身份建立（回填 + 门禁）拆至 #1316。
新旧 Server 方向兼容（纯增量可选字段，Worker 按缺失回落）；**旧 Worker（协议 < v6）
方向是门禁语义**：无 hash 且 etag 已绑的材料任务对旧 Worker 不可领——claim
admission 跳过、留队列不落失败；且 admission 与响应组装之间若发生 repair/failed-retry
complete，响应描述符快照再复核一次（material_identity.assert_worker_etag_protocol，
batch per-item drop → lease 过期重排），防止 complete 在两次判定之间落新 etag 造成
TOCTOU（codex final P1）。hash 行与 etag='' 行恒免（见 shared/protocol.py v6 条目与
jobs/queries/material_claim_gate.py 的谓词论证）。缓存寻址（codex r6）：hash 行内容寻址不变；
无 hash 行地址带上传代次（`{material_id}-v2g{代次}`，代次即身份）——repair 代次 +1 后
旧 gen 地址自然孤儿化，修复前（可能被旧 Worker 无核验缓存的）毒化字节不再命中；
代次来源与消费读取的行快照同一份（Host dispatch 现读行 / Worker claim 描述符与 presigned
URL 同行），旧 Server 描述符无代次字段时回落裸 id。恢复面（codex r4/r5 族）：ETag 失配的
ready 行经 `scripts/request_material_reupload.py` 修复——翻回 uploading、代次 +1、轮换
v2g key 并为**原行**签发受代次保护的重传 URL（hash/无 hash 统一由原 material_id 愈合，
历史 job 按 material_id 消费重传后的新对象）；S3 快照恢复（aws s3 cp 改变大行 ETag）后
启动调度前必须跑 `scripts/rebind_material_etags.py` 重绑（字节核验不过 fail-closed），
见 backup-restore-runbook.md。

#### 6.4.1 无 hash 材料的身份状态机（#1279；#1312 第 11 轮按方案 B 收缩）

**不变量（本 PR 范围）**：**已绑定 etag** 的无 `content_hash` 材料，其字节在每个消费入口都
必须与绑定的 etag 一致才可消费（If-Match，412 → MaterializeError，不落缓存）；hash 行由
sha256 fail-closed 兜底（既有）。新 complete / inline / demo seed 的行都原子落 ETag，所以
升级后签出的 PUT URL 所对应的行一律进入核验链。

**存量行（etag=''）不在本 PR 建立身份**：它们在每个消费入口维持升级前的 size-only 语义——
不 fail-closed、不挂起、不对任何 Worker 门禁。**一次性残留窗口** = 升级前签出、升级时仍
未过期的 PUT URL（presign 有效期 1h，故最长 ≤1h）：其持有者在窗口内同尺寸覆盖存量无 hash
对象不会被检出，与升级前等价、不随时间扩大。存量行的身份建立（静止期回填、未绑定档门禁、
驱动方与失败处置语义）拆至 #1316。

**消费入口维度**：材料字节只在下列入口被消费（没有预览/下载字节端点；complete 的 sha256
流读是生产侧核验，不是消费）——
- E1 Host 本地单材料 / E2 Host 本地 bundle / E3 Host 本地分片：普通节点与本地 shard 都经
  `build_runtime` → `material_cache.material_runtime_block` / `material_bundle_cache` 的
  `open_verified_stream`（同一条路，下表合为一列）；
- E4 远程 claim 准入：`claim_admission` 的 v6 协议门禁（只对 < v6 Worker 生效）；
- E5 claim 响应：`material_claim_block` / `bundle_claim_block` 下发描述符（etag、代次、
  presigned GET，memory-only）；
- E6 Worker 拉取：`worker/material_fetch` / `bundle_fetch` 对 presigned GET 带 If-Match；
- E7 两侧缓存命中：不重验（填充时已验），无 hash 地址带代次。

| 行状态（status × hash × etag） | E1–E3 Host 本地物化 | E4 准入（< v6 Worker） | E5 claim 描述符 | E6 Worker v6+ 拉取 | 机制 |
| --- | --- | --- | --- | --- | --- |
| hash 行（任意 etag） | sha256 fail-closed | 放行（sha256 兜底） | 下发 | sha256 fail-closed | 内容寻址 + 下载重算 |
| 无 hash，etag 已绑（complete/rebind），URL 全死 | If-Match 核验 | **门禁拦**（留队列，不落失败） | 下发 etag | If-Match 核验 | v6 门禁 + If-Match |
| 无 hash，etag 已绑，仍有活 URL（签发 1h 内）被覆盖 | 412 → MaterializeError（节点 failed，可 repair） | 门禁拦 | 下发 etag | 412 → 失败（可 repair） | If-Match 检出 + repair |
| **repair/failed-retry 后刚落的新 etag**（TOCTOU：准入 → complete → 响应） | 若 repair 中：拒绝（not ready）；若已 complete：If-Match | **响应描述符快照复核拦**（batch per-item drop → lease 过期重排，非硬失败） | 同左 | 若准入后已 complete：412 检出 | 快照与下发一致（material_identity.assert_worker_etag_protocol） |
| 无 hash，etag=''（存量行） | **size-only 物化**（base 语义） | 放行 | 下发 etag='' | **size-only**（无条件 GET） | 方案 B：残留窗口 ≤1h，一次性 |
| uploading/failed（任意身份） | 拒绝（not ready，节点 failed，既有） | 门禁按 etag 判 | 拒绝（claim 500 → 回收重排，既有） | — | 解析链只收 ready |
| expired | 拒绝（既有） | 同上 | 拒绝（既有） | — | 既有 TTL 语义 |
| repair 窗口（清 etag + 轮换 key + 签发新 URL） | 行为 uploading → 同上；重传 complete 后落新 etag | 同上 | 同上 | 新 key If-Match | 代次守卫 + 轮换（bare complete 409/缺对象 failed） |
| rebind 窗口（恢复后 etag 漂移，字节已核验） | CAS 前 412；CAS 后正常 | 门禁拦 | 下发新 etag | 同 E1 | verify-then-CAS（字节不过 fail-closed；CAS 四钉 etag/key/代次/status，扫描后被并发改写按竞态失败报告，#1313） |
| 滚动升级（Host 先升，旧 Worker 在役） | 不受影响 | 已绑行任务挂起、其余照领 | — | — | 见 remote-execution-runbook §5 |

构造性不可达格：「读 A 代址取 B 代对象」不可达——缓存地址代次与 presigned URL 取自同一
行快照（Host 现读行 / claim 描述符同行）；etag 绑定后对象再被改写只有「已签发 URL 复用」
一条路，而 If-Match 把该路变成 412 检出而非静默。

已声明接受格（M30 对账）：
- **pre-complete 幽灵写绑身份**（能力模型边界）：作者 PUT 之后、complete 的 HEAD 之前，
  活 URL 持有者可同尺寸重写并被绑成身份——presigned URL 即作者通道，URL 外泄不在威胁
  模型内；hash 行免疫（sha256 声明在先）；无 hash 行结构性「最后写入者即作者」，关不掉
  （除非强制 content_hash）。
- **流中同尺寸撕裂**（存储契约）：If-Match 只校验 GET open 时刻；流读期间的同尺寸覆盖
  依赖存储的「单 GET 原子快照」语义（AWS S3 保证；已写入
  docs/materials-storage-deployment.md 的后端契约要求）。hash 行由最终 sha256 复算免疫，
  仅无 hash 行依赖此契约。
- **存量行残留窗口**（方案 B）：见上；升级前已被毒化的存量行同样无机制可分辨（与 base
  等价），repair 重传可换内容身份。
- 信任前提：rebind 对无 hash 行只 size 核验（信任运维备份字节）；worker 版本自报
  （trusted infra）。

#### 6.4.2 材料对象写入协议 × S3 孤儿 GC 材料删除批（#1314）

**不变式**：任何被 GC 删除的材料前缀对象，在删除时刻及之后都不可能被任何 materials 行
（含 uploading intent）引用。

**写端协议（行先于对象）**：每个材料对象 key 在第一个字节写入之前，已被一条**已提交**的
materials 行引用；key 由新 uuid（material_id）或行锁内单调递增的代次派生，行删除或迁走
后再无写者能让行重新引用它。写入入口全集（`tests/services/test_material_write_surfaces.py`
按 AST 钉住 `put_object` / `presign_put` 调用点）：

| 入口 | 写字节的方式 | 行先于对象的落点 |
| --- | --- | --- |
| presign 首发（`MaterialsService.presign`） | 浏览器经 presigned PUT（1h） | insert uploading 行提交后才签 URL |
| presign 重试（同 hash 非 ready 行） | 同上，新代次 key | 行锁内代次 +1、派生 `v2g{n+1}` key，提交后才签 URL；旧 URL 指向死 key |
| repair（`request_material_reupload`） | 同上 | 行锁内 ready→uploading、代次 +1、轮换 key，提交后才签 URL |
| inline 文本（`run_text_batch`） | 服务端 `put_object` | chokepoint `material_object_publish`：无 hash uploading intent 行 → PUT/HEAD → 同事务删 intent + 插 ready（CAS：intent 必须仍在） |
| demo seed（`demo_material_seed`） | 同上 | 同上（`on_non_ready='skip'`、不设 TTL） |
| complete / rebind / bundle 创建 | 不写字节 | — |
| Worker | 不写材料前缀（只写 `jobs-staging/`） | — |
| 备份恢复（aws s3 cp/sync） | 运维 | 模型外：恢复纪律见 backup-restore-runbook |

**GC 判定入口**：`scripts/gc-s3-jobs.py` → `s3_material_gc.collect_gc_report`（`scan_orphans`
两个 jobs 前缀 + `scan_material_root_orphans` 根前缀，扫描期用一次性载入的 key 集合；撞名
前缀里的材料形态 key 分流进材料候选；`partition_material_orphans` 三分为进删除批 / 暂缓 /
旧文法只报告）→ `--apply` 才执行 `s3_jobs_gc.apply_gc`（jobs 两条道，**不删任何材料形态
key**）与 `s3_material_gc.apply_material_gc`（材料道）；dry-run 只报告。材料道三道门：
①单次使用文法；②对象年龄过 `max(--grace-hours, MATERIAL_MIN_GRACE_HOURS=1h)`，删除动作处
按当时时钟复核；③每批删除前新鲜反查（无反查整道不删）。门禁钉在删除函数本身，手工构造的
列表同样受约束。
其余删除入口：`MaterialsService.delete`（行锁内先删对象后删行）、TTL 回收（先删行后删
对象）、chokepoint 补偿（先删 intent 行后删对象）、bucket lifecycle（运维，按年龄）。

**交错决策表**（写者处于某状态时 GC 走到扫描 Sc / 反查 Rv / 删除 Dl；`--grace-hours 0`）：

| 写者状态 | 对象 | 引用 K 的行 | GC 结论 | 判定 |
| --- | --- | --- | --- | --- |
| chokepoint：intent 已提交、未 PUT | 无 | intent | 无可删 | 安全 |
| chokepoint：PUT 后、发布前（含卡住远超 1h） | 有 | intent | Sc 若缓存早于 intent 可判候选；Rv 见 intent 救下 | 安全（故障注入用例） |
| chokepoint：发布事务提交中 | 有 | intent 或 ready（同事务换手） | Rv 只读已提交状态，二者必居其一 | 安全 |
| chokepoint：复用 ready 胜者 / skip / 失败补偿删行后 | 有 | 无 | 可删 | 安全：K 的 id 不复用，永不再被引用 |
| chokepoint：intent 被用户删除（删除 API 连对象一起删） | 可能被迟到 PUT 重建 | 无 | 可删 | 安全：发布 CAS 拒绝，不会有 ready 行指向 K |
| chokepoint：intent 被用户 complete 成 failed/ready | 有 | 该行 | 保留 | 安全：发布 CAS 409，补偿只删 intent 形态行 |
| chokepoint：release 事务失败 / 进程崩溃 | 有/无 | intent 残留 | 保留 | 安全；残余：uploading 行待用户删除 |
| presign：URL 签出后过期未用、行永不 complete | 有/无 | uploading | 保留（intent，不是孤儿） | 安全；残余同上 |
| presign：complete 核验失败翻 failed | 有 | failed | 保留 | 安全 |
| presign 重试 / repair 轮换后的旧代 K（旧 URL ≤1h 仍可迟到 PUT） | 有 | 无 | 删 | 安全：代次单调，行回不到旧 key |
| Sc 后才发生的重试 / repair 轮换 | 有 | Sc 时有、Rv 时无 | 本轮不进候选，下轮删 | 安全（扫描缓存只会少删） |
| 行被删除 API / TTL 回收后的残留或幽灵 PUT（URL ≤1h） | 有 | 无 | 删；GC 后再 PUT 只是新孤儿 | 安全：id 不复用 |
| #1312 期旧 Host（先 PUT 后建行，v2g key）窗口 < 1h | 有 | 无（暂未建） | 年龄下限暂缓 | 安全（纵深） |
| 同上，PUT 后卡住 > 1h 才建行 | 有 | 删后才出现 ready 行 | 删 | **不安全：声明残余** |
| 旧文法 key（`g{n}` / legacy 无代次段 / `inline-{uuid}`） | 有 | 无 | 只报告 | 安全（不删，#1282 纪律） |
| 撞名前缀（workspace 名 `jobs`/`jobs-staging`）里的材料形态 key | 同上各行 | — | 同材料道门禁 | 同上各行 |

Rv→Dl 之间不会冒出引用 K 的新行：协议内写者只为「尚不存在对象」的 key 建行（新 uuid 或
新代次），而 K 在 Sc 时已有对象；唯一的例外是协议外的旧 Host，由年龄下限覆盖（Dl 时对象
年龄 > 1h ⇒ 其 PUT→建行窗口 > 1h）。在途 claim 持有已轮换旧 key 的 presigned GET 会读到
404 而非 412——repair 只轮换已判毒化的对象，两者都是失败结局，不扩大语义。

**声明的残余窗口**：①#1312 期 Host 与本版 GC CLI 混跑且其 inline/demo seed 在 PUT 与建行
之间卡住超过 1h（默认部署里 GC CLI 随 Host 镜像同版本运行，不出现此组合）；②写端崩溃
或 release 失败留下的 uploading intent 行及其对象不被 GC 回收（与浏览器放弃上传的
uploading 行同类，用户删除行即回收）；③模型外：DB 从旧备份恢复会复活引用已删对象的行
（恢复纪律：S3 与 DB 同步恢复、核对后再跑 GC）；bucket lifecycle 的按年龄删除。

SigV4 presigned PUT 无法约束 Content-Length，size/hash 一律由 complete 时
服务端 HEAD + 流式校验强制（v1 实现为完整读自算 sha256，简单可靠；上传端
带 checksum 头后可免读）。`(workspace_id, content_hash)` 的 dedup 唯一性
以部分唯一索引实现（`content_hash` 可选，空串不参与唯一）。

签发地址与直连地址分离：后端用内部 endpoint（`AGENT_LEGION_S3_ENDPOINT`，
compose 内 `http://seaweedfs:8333`）做 HEAD/GET/DELETE；签发给浏览器 /
remote worker 的 presigned URL 用 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`
（未配置则回落内部 endpoint）——SigV4 把 Host 签进签名，URL 必须以
客户端实际可达的地址签发，不能签后改写 host。

文件夹上传用 `webkitdirectory` 递归读，逐文件走同一协议，客户端并发控制。

### 6.5 job 产物统一进对象存储（D12）

**已落地（#160，schema v54）**。材料与产物是同一种东西的两端——都是
「workflow 要读写的字节」。统一存储后：

- **job_dir 降级为执行暂存**：沙箱 `--cwd` 语义不变，节点照常写 job_dir；
  节点完成时由执行面把产物上传对象存储（key 布局
  `jobs/{workspace_id}/{job_id}/{name}`，`job_artifacts` 表为权威清单；#853 起
  改为不可变版本 key `jobs/{workspace_id}/{job_id}/.v/{version}/{name}`、同名重登记
  不覆盖旧对象，见 [artifact-direct-url-pinning.md](artifact-direct-url-pinning.md)）。
  本地 job_dir 成为可淘汰缓存：淘汰只删 `job_artifacts` 已确认的文件、
  只限 completed 且无活跃 lease 的 job，容量配置
  `AGENT_LEGION_JOB_CACHE_MAX_BYTES`（默认 50 GiB，对照材料缓存先例）。
  体积治理从「给文件系统打补丁」变成「给缓存配容量」。
- **上传时机与失败语义（#160 决策）**：节点完成即传（增量持久化）；
  上传失败不 fail 节点——本地副本保留，后台 reconciler 补传；S3 未配置
  时全特性惰性关闭。产物清单维持节点声明制（`outputs`），不做全量上传。
- **remote worker 的产物回传与材料下发走同一条 S3 通道**（presigned
  PUT/GET 随 claim 注入，Host HEAD 核验后登记并落盘 job_dir），不再有
  「产物怎么从 worker 回 host」的独立协议；`/api/artifacts` 的本地 CAS
  路径保留供旧 Worker 与 legacy blob 读取，标注 deprecated。
- 产物进对象存储后，打包重设计（#120，§10）的「选择性打包部分
  artifacts」就是「按清单从 bucket 取文件生成 zip」，天然成立。
- 迁移期兼容：老 job 的产物仍在本地 job_dir，读路径按「本地缓存命中
  直读、缺失回退对象存储」解析，不搬历史数据。例外是 quality
  artifact_contents：刻意 manifest-first，直接读对象存储的持久化记录
  （回退 legacy CAS），不读本地 job_dir，保证质量评审反映落盘权威副本。

评审修复后的加固语义（#162）：

- 淘汰守卫收紧：除 size 匹配外，清单行带 content_hash 时复核本地
  sha256 一致才认证；每个 job 首次 unlink 前重查 job 仍为 completed
  且无 active lease。
- presign PUT/GET 过期时间按节点 timeout 派生
  （`max(3600, timeout_seconds + 900)`，缺省 600），只在 claim 时内存态
  注入，不进日志/DB；Host HEAD 核验含体积上限（实例设置
  `agent_workers.max_archive_bytes`）；cancelled 结果的部分产物经 Host
  流式 sha256 核验后登记。
- Worker 直传失败（或 spec 畸形）清 `artifact_uploads` 后回落 CAS
  通道；本地 rerun 沙箱运行前把缺失的声明输入从对象存储回填
  （`server/app/executors/artifact_restore.py`，hash 校验、逐文件容错）。

### 6.6 可平行迁移到 Amazon S3

代码只对 S3 API 编程（presign、GET/PUT、multipart、lifecycle），不依赖
任何后端特有功能。因此部署后端可以平行替换：自研/私有化用 SeaweedFS
（或 RustFS/MinIO/Garage），上云直接切 Amazon S3，只改 external_connections
的 endpoint 与凭据配置，零代码变更。数据搬迁用 bucket 间同步工具
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
  同步；`agent_catalog/builtin.py`（Agent 模板种子，实现里以
  `from server.app.agent_catalog import builtin as agent_catalog_builtin`
  引用）不受影响；
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
- 材料侧 TTL（已随 #160 落地）：实例设置 `materials_ttl_days`（默认 0
  关闭）→ complete 写 `materials.expires_at` → sweeper 到期翻 `expired`、
  引用计数为 0 且过 grace 才物理删除；bucket lifecycle 为孤儿兜底
  （运维细节见 docs/materials-storage-deployment.md）；
- job_dir 作为可淘汰缓存配容量上限即可，历史 job 产物全量保留的问题
  随产物上云自然消解；本地存量在 #120 落地前手动清理。

## 11. 分阶段实施

| 阶段 | 内容 | 用户可见变化 | 状态 |
|---|---|---|---|
| v1 | materials 表 + S3 存储 + presigned 上传 + 物化缓存；ref 条目；run 概念与路线 A 迁移；「添加条目」面板；demo seed 迁移（§9） | 上传/粘贴 ID 提交任务，intake 消失 | 已落地（schema v53） |
| v1.1 | 场景 A 路径输入（拷贝进材料区） | 本地路径提交 | 待实施 |
| v2 | workflow 输入契约声明；question/video 导入改造为 connector 形态；场景 C 原地引用 | workflow 声明更直白 | 待实施 |
| 并行 | 产物上云后的打包重设计（Issue #120） | prod 体积受控、出站回传 | 见 Issue #120 |
| v1.2 | 文件夹作为单 job 输入（bundle 条目，manifest 引用式，§5.4，#156） | 「添加条目」支持文件夹整体打包 | 已落地（schema v55） |
| v1.3 | 直接输入需求（text 条目，服务端落成 Markdown 材料，§4.1） | 「添加条目」支持直接输入需求文字启动 | 已落地（0.7.14，#761） |
| v1.4 | start 节点 `text_input`（输入框标题 / 落盘文件名 / 预填模板，§4.1） | 「输入需求」按工作流预填需求模板，模板未改不能提交 | 已落地（0.7.14，#764） |
| future | 需求文本 + 附件合成一个 bundle 条目 | — | 待实施 |
| future | connector 实体化 | — | 待实施 |
| future（已立项，方案待讨论） | **异步建 job 的进度与结果可见性**：万级 job 走异步队列创建时，界面只看到数量上涨，看不到创建进度与结果分布（成功 / 因重复被 dedup / 校验失败及原因）。需求：run 维度展示创建进度条与结果明细。具体方案另行讨论后补本节（#1117 runs 异步模式的服务端核心已落地，见 [runs-async-mode.md](runs-async-mode.md)：202 + batch 句柄 + feeder 滴灌；Studio 进度 UI 归后续切片） | — | 待讨论 |

## 12. Quality Impact

- **新增 invariant 候选**（实施时同步 `config/architecture/`）。登记现状：RUN-FREEZE-001、
  MATERIAL-ACCESS-001、MATERIAL-SECRET-001、MATERIAL-BUNDLE-001 已注册（另有实施中新增的
  MATERIAL-INLINE-OWNERSHIP-001）；INTAKE-RETIRE-001 未注册为 invariant；CONNECT-DIRECTION-001
  未注册、连接方向字段未实施：
  - INTAKE-RETIRE-001：job 创建只经 RunService，禁止新增 source_kind /
    resolver 形态；
  - RUN-FREEZE-001：冻结配置与 pins 以 job/run 列为权威，禁止回读
    batch payload 形态；
  - MATERIAL-ACCESS-001：节点访问材料只经物化缓存静态 allow-read，
    禁止动态放行任意宿主路径；
  - MATERIAL-SECRET-001：材料存储凭据只走 env-only infra 注入（同
    `database.url`），不落 tracked yaml / API / 日志。
  - MATERIAL-BUNDLE-001：bundle 是不可变引用式 manifest，删除双向守卫，
    物化为确定性地址的硬链接目录树，Worker 复用材料 presigned 通道。
  - CONNECT-DIRECTION-001：external_connections 必须声明方向；ref 只许
    引用 source 连接，出站节点只许引用 sink 连接，服务端强校验。
- **测试**：新测试放 `tests/services/`（RunService、材料 dedup、迁移）、
  `tests/routes/jobs/`（条目 API、上传协议）、`tests/executors/`（物化
  缓存与沙箱 allow-read）；不新增 `tests/` 根文件。路线 A 迁移测试用
  旧 payload fixture 做行为等价断言。
- **安全面**：presigned URL 限 workspace 作用域与过期时间；complete 时
  服务端校验 size/hash 防声明与实际不符；ref 的 connection_key 校验
  workspace 可见性；材料下载走 `download.py` 的 SSRF 守卫。
- **风险**：本地后端选型可变（RustFS → SeaweedFS，#340）→ 代码只对 S3 API
  编程，可换 MinIO/Garage/AWS S3；迁移涉及全量历史行 → 迁移脚本必须幂等、
  可重入，先在 prod 副本上演练。
