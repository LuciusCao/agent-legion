# 执行代次协议（EXEC-GENERATION-001）与三平面一致性

本文是执行代次协议（execution generation protocol）的现行架构文档：它描述
workflow 升级/重跑与并发执行之间的串行化机制、执行态平面的一致性论证，
以及审查同类并发改动时复用的对抗审查 checklist。invariant 注册表的正式表述
见 `config/architecture/architecture-invariants.yaml` 的 EXEC-GENERATION-001
条目；反向审计证据见
[workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)
的同名行。本文不写行号——以模块级文件引用为准。

## 1. 问题背景（issue #759）

Job 的执行状态由调度（claim/enqueue）、执行回报（finish/fail）、人工审批、
清扫器（sweeper）与突变操作（rerun / run-to / workflow upgrade / approval
rework）多方并发读写。突变操作会重置执行现场（节点状态、产物清单、分片行），
而在途的旧操作可能在突变提交后才落地。重构前存在三类竞态：

1. **升级覆盖执行中状态**：workflow upgrade / rerun 重建 `job_nodes` 行时，一次
   旧的 claim 或 finish 可能在重置完成后才把节点翻回 running/completed，覆盖
   新现场；或按已被重置抹去的旧 revision 继续执行。
2. **重跑与在途 lease 交错**：迟到 finish、迟到 failure 记录、心跳过期清扫、
   孤儿 running 行恢复等边缘写路径各自为政，没有一个统一的「这次写属于哪一轮
   执行」判定；旧轮次的清扫甚至会把新轮次重建的 pending 行翻成 failed。
3. **批路径死锁**：批操作（finish_many、批 claim、sweep）与 mutation 侧以不同
   顺序访问同一批 job 的行锁与 advisory 锁，交叉即 AB-BA 死锁（40P01）。

协议的目标不是消灭并发，而是给每个执行态写面一个共同的**代次（epoch）判据**：
突变把 job 推进到新一代，一切持旧代次的写面在新一代面前自动放弃或降级为
收尾-only。

## 2. 协议定义（EXEC-GENERATION-001）

### 2.1 代次列（schema v85）

`jobs.execution_generation` 是单调递增的执行代次权威源，镜像到各写面自有
的行上（DDL 在 `server/app/db/migrations/execution_generation.py`，apply-fn
风格，幂等）：

| 表 | 盖戳时机 | CAS 语义 |
| --- | --- | --- |
| `jobs` | 突变事务内 +1（唯一 bump 源） | 所有比较的现值基准 |
| `job_nodes` | 重置/重建时盖新戳；park approval 盖当前戳 | recover / not_applicable 批写按戳判别归属 |
| `node_runs` | claim 时盖戳 | finish 的 CAS 比较 lease 戳而非本行 |
| `executor_leases` | claim 时盖戳 | finish 比较 lease 戳与 jobs 现值 |
| `agent_execution_requests` | enqueue 时盖戳 | claim / sweep 比较请求戳与 jobs 现值 |

`default 0` 让 v85 之前的存量行天然一致：在途旧 lease 与其 job 同为代次 0，
新增 CAS 对其放行，行为不变。

### 2.2 突变侧：唯一锁域与 bump 点清单

每个重置执行状态的突变都运行在 `lease_guarded_mutation`
（`server/app/jobs/atomic_mutations.py`）事务内，事务**首句**取全库统一的
per-job advisory 锁 `pg_advisory_xact_lock(hashtext('job-mutation:' || job_id))`，
随后复查 active lease / running 节点（`JobMutationConflict(busy)` 拒绝）。突变
侧只取本锁、**不取任何池级锁**。bump 恰好一次，fold 进突变自身的 jobs UPDATE
（`returning execution_generation`），重置/重建的 `job_nodes` 行盖同一戳：

| 入口 | bump 点 | 说明 |
| --- | --- | --- |
| rerun / approval rework / run-to-with-start | `mark_nodes_for_rerun`（`server/app/jobs/atomic_mutations.py`） | 共用的唯一 bump 点；同事务取消受影响节点的 queued agent 请求（`_cancel_queued_sql`，含 manifest trim）、删分片行、删被暂存产物的 `job_artifacts` 清单行 |
| run-to（无起始节点） | `apply_run_to` → `set_run_to_control(bump_generation=True)` | run-to-with-start 同事务已由 `mark_nodes_for_rerun` bump，这里不再 bump——整事务恰好一次；重置集 = closure ∩ 非 completed，同事务暂存其产物并删清单行 |
| workflow upgrade（clean） | `upgrade_job_workflow`（`server/app/jobs/workflow_upgrade_mutation.py`） | fold 进 revision 切换的 jobs UPDATE；bump 先于节点行重建，重建行盖新戳；节点集合整体替换，同事务按 job 作用域了结全部 queued 请求（`cancel_queued_requests_for_job`）——遗留行无人 claim 时会把 `has_active_request` 的闸门外重派无限期挡住；产物失效按名（旧 output − 新 output − 新 input），清单行**按暂存名精确删除**（同 rerun）——「保留 ⇔ 未暂存」构造性成立，无独立 preserve 集；旧产物名的存亡由按名闭包**唯一**判定，被删节点只清 run history（`include_outputs=False`），不按旧定义重枚举 outputs——被删生产者的产物若已转移为新输入，枚举会把种子误暂存、消费者永久无法 ready；node_shards 按 job 作用域删除 |

不 bump 的突变：resume、delete、approval park（park 只盖当前戳，自身不推进
代次）。delete 不需要了结 queued 请求：`agent_execution_requests` 对
jobs/workspaces 均为 on delete cascade，行随删除物理消失。未被重置的节点行
永不改戳——这是审批门语义成立的前提（见 2.4）。

### 2.3 执行态写面：CAS 面清单

每个写面先与突变侧互斥（取同一把 `job-mutation:<job_id>` 锁），再把自己携带的
期望代次与锁下重读的 `jobs` 现值做 CAS。**fail-closed：对不上就不写**（或只
收尾不翻转）。共享 helper 是
`_lease_control.lock_job_mutation_and_read_generation`
（`server/app/executors/_lease_control.py`）：取锁 + 读现值一步完成，jobs 行
不存在返回 None，任何期望代次都算过期。

| 写面 | 位置 | 过期语义 |
| --- | --- | --- |
| code 池 claim | `claim_lease`（`server/app/executors/_lease_claims.py`） | 拒绝 claim，零写入；claim 成功时给 `node_runs` / `executor_leases` 盖当前戳 |
| agent/远端 claim | `evaluate_candidate`（`server/app/agent_broker/claim_evaluate.py`） | 按突变侧同款语义取消 queued 请求（终态 + manifest trim），节点保持 pending，下一调度轮以新代次重新入队 |
| agent 请求入队 | `enqueue_request`（`server/app/agent_broker/enqueue.py`） | 打包（按代次 N 评估）与 INSERT 之间夹着 bump 时直接不插入；否则无人 claim 的过期 queued 行会把重派挡在 `has_active_request` 之外 |
| finish（含分片臂） | `finish_lease`（`server/app/executors/_lease_lifecycle.py`） | 锁内重读 lease 仍 active 才收尾（codex #774 P1）；lease 释放与 `node_runs` 终态照常，但跳过 `job_nodes` 翻转、`sync_job_status` 与 until_node 暂停 |
| fail_without_lease | `server/app/executors/_lease_config_failure.py` | 整个配置失败记录跳过，节点保持 pending 等下一轮 |
| approval park | `park_awaiting_approval`（`server/app/executors/_lease_approval.py`） | 评估代次过期则跳过 park；成功的 park 给 gate 行盖当前戳 |
| approval 决策 | `approve_gate_atomic` / `reject_gate_atomic`（`server/app/jobs/queries/approval_decisions.py`） | 锁下状态守卫（`ApprovalGateConflict`）；**刻意不做代次比较**，见 2.4 |
| lease 过期清扫 | `expire_stale_leases`（`server/app/executors/_lease_expiry.py` 等） | 过期代次的回写跳过 |
| 孤儿 running 恢复 | `recover_orphaned_running_jobs`（`server/app/executors/_lease_write_paths.py`） | 只认盖了现值戳的 running 行，旧戳行拒绝复位（防永久卡 running 的闸门由 claim 盖戳保证） |
| agent sweeper | `sweep_expired_claims`（`server/app/agent_broker/sweepers.py`） | 过期代次：lease/node_run 对账照常，请求**取消而非 requeue**（requeue 会双跑），`job_nodes` 绝不动 |
| 两个 queued-request sweeper | `fail_stale_definition_requests`（`server/app/agent_broker/sweeper_definitions.py`）、`fail_unclaimable_model_requests`（`server/app/agent_broker/unclaimable.py`） | 无锁扫描 + 逐候选 `sweep_generation.lock_sweep_candidate` 前奏（advisory 锁先于请求行 FOR UPDATE，防 AB-BA）；过期请求按突变侧语义取消，同代次保留原 fail 语义 |
| 分片 fan-out | `materialize_shards_guarded`（`server/app/workflow_worker/shard_fanout.py`） | 锁 + CAS 在事务最前（先于任何 `node_shards` 写）；过期则跳过物化与空 fan-out 完成，节点等下一轮 |
| ready-gate not_applicable 批写 | `mark_nodes_not_applicable_many`（`server/app/jobs/queries/job_node_lifecycle.py`） | 每条目携带评估时读到的代次，批内逐 job 锁下重读，过期条目跳过（跳过的条目下一轮重评估——bump 改了扫描 mark） |
| 调度脏跟踪 | `list_active_job_marks`（`server/app/jobs/queries/job_scan_marks.py`） | 扫描 mark 列含 `execution_generation`：bump 即 mark 变化，缓存的 ready 候选自动失效 |

### 2.4 审批门：为什么移除代次比较

bump 是无条件全局的——分支 B 的一次 rerun 也给整个 job +1——而重置只给闭包
内的节点行盖戳。如果决策侧比较「gate 行戳 == jobs 现值」，兄弟分支的 bump 会把
未被重置的已 park gate 误判过期，而 `awaiting_approval` 不可重新 park，等于永久
brick。现行语义：决策写在 `job-mutation` 锁下只做状态守卫——gate 自身被重置时
状态先离开 `awaiting_approval`（pending/stale），状态守卫拦住针对旧 gate 的迟到
决策；重新 park 后到达的决策面向新一轮待审，接受即正确。

### 2.5 锁序与批序全序

锁序是写死的不变量（advisory xact 锁提交才释放，savepoint 回滚不释放）：

| 层级 | 锁域 | 持有者 |
| --- | --- | --- |
| 1（最外） | 池级锁：`code-pool` / `agent-ws:<workspace_id>` / `agent-worker:<worker_id>` | claim 与批路径；mutation 侧**永不取** |
| 1.5 | `artifact-authority:<key>` | promote 事务首句（多 key 升序），串行化产物字节面的备份/copy/登记/恢复；mutation 侧不取，与池锁无共持 |
| 2 | `job-mutation:<job_id>` | mutation 侧首句；所有执行态写面在池锁之后（或无池锁直接）取 |
| 3（最内） | 行锁（`FOR UPDATE` 等） | 各写面自身 |

无环论证：mutation 侧不取池锁也不取 artifact 锁，跨层只有单向边
（artifact-authority → job-mutation；池锁 → job-mutation）。enqueue 不持
池锁、直接取 job-mutation，环保持无环。

**批序全序**：每个跨 job 的批（finish_many、try_claim_many、expire、recover、
agent sweep、两个 queued-request sweep、批 claim 的每个候选——code 也包括，
借 ws 锁键纯当排序位，code 本不取 agent-ws 锁）一律按全库唯一序
`(hashtext('agent-ws:' || workspace_id)::int, job_id)` 遍历；jobs 行已消失的
孤儿项用哨兵 `-(2**31)` 定位（`_ORPHAN_WS_LOCK_KEY`），job_id 收尾决胜。单一
全序保证任意两批不会以相反顺序走同一对 job。

### 2.6 下游消费闭包：显式边 ∪ 隐式 input 消费边

「哪些节点必须跟着重跑」依赖下游闭包的正确性。loader 不要求 `inputs` 的生产者
有显式边，而调度器纯文件驱动（输入文件出现即解锁）——`p.outputs=[x]`、
`q.inputs=[x]` 无边时，q 若遗漏出闭包会被静默继承旧 x。统一邻接表在
`server/app/workflows/workflow_consumption.py`：output 名 → 生产者索引构成
producer→consumer **隐式消费边**，与显式边合并（RMW 不自边但传播至其他
consumer，无生产者的外部 input 不产生边，隐式边可能成环、环内互染是保守
方向）。rerun / run-to / approval rework 的下游计算一律走
`dependency_downstream` / `dependency_children`，不允许各自重遍历定义。

这是代次协议的前提：闭包划错，CAS 护住的现场本身就是错的。upgrade inherit
模式的种子+传播闭包在同一邻接表上构建（后续 upgrade-inherit 层接入）。

### 2.7 写面登记与静态强制（EXEC-GENERATION-002）

本协议经多轮对抗审查收敛后，同一类残余风险只剩一种形态：新写面绕过共享
helper 直写执行态。因此写面全集由机器钉住
（`config/architecture/execution-write-surfaces.json`，检查脚本
`scripts/architecture/execution_write_surfaces.py`，接入
`scripts/check_architecture.py` 管线）：

- **执行态写面**：字符串 SQL 中对 `jobs` / `job_nodes` / `node_runs` /
  `executor_leases` / `agent_execution_requests` 五表的 `insert into` /
  `update`，模块级白名单 `state_write_modules`；
- **产物字节写面**：artifact key 的 `put_stream(` / `copy_object(` 调用点，
  白名单 `artifact_byte_write_sites`（storage 层自身实现豁免——它是抽象
  本体而非写面）；
- **产物清单行写面**：`upsert_artifact_row_tx` / `ARTIFACT_ROW_UPSERT_SQL`
  引用点，白名单 `manifest_row_write_sites`（helper 定义模块豁免）。

命中注册表外的写面即 CI 报错；注册表条目对应的写面消失（文件删除或重构）
同样报错——注册表与实际扫描双向一致，防漂移。新增执行态写面的正规通道：走
`lease_guarded_mutation` / `lock_job_mutation_and_read_generation` /
`upsert_artifact_row_tx` 等共享入口，并把条目（含 `via` 指向的 helper）加进
注册表；机器检查是兜底，§3 的人工切面（降级语义、锁序、批序）不变。

### 2.8 产物字节与清单平面：统一 promotion 协议

在 finish CAS **之前**落地的产物写口（Worker 回传 promote、本地 code 执行的
D12 镜像上传）与 finish 内的清单登记共用同一个 primitive
（`server/app/executors/_artifact_promotion.py` 的
`promote_to_authority_guarded`）：

1. **staging 先行**：字节永远先落 staging key，**绝不直写 authority key**——
   本地臂每次调用生成独立 attempt 命名空间（并发同 lease 重试的锁外
   `put_stream` 互不覆盖、finally 清理互不误删，codex #774 P1）；远端臂
   沿用协议固定的 per-execution key（重试携带相同 refs、字节相同），且
   **promote→finish 提交之间的窗口内绝不删 staging 源**——并发 /result
   重试在此窗口仍要 verify/promote 它，先提交者删源会把已完成节点冤判成
   failed（#774 对抗复审 P1）；staging 源只在 finish 提交后由完成方删除，
   其余结局的残留交 bucket lifecycle / `s3_jobs_gc`。
   既有 authority 对象先 server-side 备份到**按调用唯一化**的回滚 key
   （attempt 维度——先提交者的锁外清理删不到并发重试者的备份，codex
   #774 P1）；
2. **按 key 串行**：同一 authority key 的并发 promote 经
   `artifact-authority:<key>` advisory 锁（升序、任何字节操作之前取）全程
   互斥——备份、copy、权威复查与失败恢复（commit 时刻失败除外，见 §4）
   都在同一事务的同一把按 key 锁内，过期 promote 的恢复在构造上不可能插进
   新代次 promote 的 copy 与登记之间（#759 复审 P1-C）。copy 仍不持
   `job-mutation` 锁（大字节量可中断；mutation 侧不取 artifact 锁，不被阻
   塞）——锁序 artifact-authority:* → job-mutation:*；
3. **锁内单事务权威复查**（`register_rows_guarded`）：取 `job-mutation` 锁 →
   复查 lease 代次（`lease_artifact_write_current`：lease 行存在、仍 active、
   落戳代次 == jobs 现值——判活谓词与 `finish_lease`/broker 清扫完全同源，
   **不按 `expires_at` 单独判死**：心跳饥饿但控制面新鲜的 Worker 由
   HeartbeatDeferral 刻意保留 lease，闸若按时间戳关闸会把仍被承认的结果
   字节面判死、成功节点被 finish 永久翻失败，codex #774 P1；ownership 的
   唯一撤销通道是 sweeper/expiry/finish 对 lease 行的删除或状态翻转，同持
   `job-mutation` 锁与本复查互斥）→（远端臂）staged 文件落盘 → upsert 清单行。
   与突变侧只有两种序：登记先提交（随后被突变当作重置面删除），或突变先提交
   （闸拒绝登记）；
4. **失败回滚**：闸拒与 copy/登记中途失败时用回滚备份恢复已完成的
   authority-key copy（事务死亡前、仍在按 key 锁内；闸拒恢复在闸复查之后，
   `job-mutation` xact 锁随事务持到恢复完成——有界阻塞，无环），不落盘、
   不复活清单行；锁内登记抛异常时文件提升经 `FilePromotionGuard` 整体回滚、
   authority 按备份恢复后再原样上抛——旧清单行永不指向 hash/size 不符的
   字节。恢复 copy 自带与上传同策的有界重试（瞬时存储故障在原地收敛）；
   commit 时刻失败（连接死亡）是不可约例外：锁随会话释放，恢复降级
   为无串行 best-effort（§4）。

**补偿资源的删除前提**（codex #774 P1 族的结构收敛）：协议里每个暂存/备份
对象都是为防范某个中间态而存在的，**删除它的前提是那个中间态已确认解除**——
不满足前提的删除就是「清理动作抹掉最后恢复源」这族 P1 的温床。全集：

| 资源 | 防范的中间态 | 删除前提（满足其一） | 实现位置 |
| --- | --- | --- | --- |
| 回滚备份对象（`.rollback/*`） | authority 已覆盖但新状态未提交 | 登记提交 ∥ 恢复 copy 成功 ∥ 该 key 的 copy **从未被尝试**（备份冗余——ack 歧义下「尝试过但失败」必须按「可能已覆盖」进恢复集，#774 对抗复审 P1） | `promote_to_authority_guarded` finally 按 `unrecoverable` 集过滤；恢复最终失败的备份保留，ERROR 日志携带 authority/backup key 作恢复指针；`s3_jobs_gc` 对 `/.rollback/` 段豁免回收（bucket lifecycle 的子串不可豁免性见 materials-storage-deployment.md） |
| 本地臂 staging 对象（per-invocation key） | 字节未 promote | promote 终局已定（提交或闸拒）——调用方私有 key，finally 清理 | `upload_via_staging_guarded` finally |
| 远端臂 staging 对象（per-execution key，Worker 共享落点） | 字节未 promote 且并发 /result 重试仍要 verify/promote | finish 提交后由完成方删除；其余结局交 bucket lifecycle / `s3_jobs_gc` | `completion_staged.finish_staged` 尾部 |
| 文件提升备份目录（`.promote-rollback-*`） | 文件已移动但登记未提交 | 登记成功（`discard`）∥ 已**完整**回滚（`rollback` 部分失败时备份目录整体保留 + ERROR 日志带路径，失败项备份是旧目标的最后本地恢复源）；**可逆性前提**：target/source 必须是文件——真实目录在任何移动之前整批拒绝，备份后立即复查收口预检↔移动间的 TOCTOU 换形（codex #774 P2 族） | `_file_promotion.py` 预检 + 备份后复查 + `FilePromotionGuard` |

恢复 copy 的重试分级（#774 对抗复审 P2）：按 key 锁仍持有的臂（闸拒、
存储/文件/校验面失败）带界重试吸收瞬时存储故障；锁已随会话释放或正在
解栈的臂（psycopg 族异常、commit 时刻失败、非 Exception 中断）**单发**
——无串行化时的退避 sleep 只放大迟到恢复踩并发新 promote 的窗口。
补偿臂捕 BaseException：KeyboardInterrupt/SystemExit 中途也得在锁仍持有
时恢复，而不是让 finally 在中间态未解除时清掉备份。

审查任何新增清理动作时先问：它删的资源防范什么中间态、该状态此刻是否已
确认解除、删除失败/删除过早各是什么后果。

两个写口的接入点：Worker 回传 `remote_artifact_promote.promote_all` 在任何字节
copy 前先做无锁预检再走共享序列；本地上传由 `JobArtifactObjectStore.upload` 的
lease 臂把字节写到 per-invocation staging key 后走同一 primitive，循环中途落地的
reset 既登记不进去也污染不了 authority 对象。

**Worker 结果归档的本地文件平面**：`AgentCompletionHandler.finish` 把归档只解包
到 job_dir 内的 staging 目录（校验/分片读/镜像都读该视图，镜像同样携带
lease_id）；expected 输出、events.jsonl 与 node.log 的提升经
`ExecutionResult.staged_file_moves` 挤进 `finish_lease` 的代次 CAS——代次不匹配
时文件永不落盘，旧代次归档覆盖不了新现场的本地输入。

**落点形态的三层纪律**（#759 对抗复审 P2 族，codex #774 P2）：归档与 remote
ref 两个通道各自宣称的路径形状若单文件系统不可能同时成立（`reports` 是文件、
`reports/out.json` 也是文件），任何中途发现都会在其他面已提交之后炸穿结果提交。
因此：

1. **预检**（`agent_control/completion_preflight.py`）：任何字节移动之前核算全部
   计划落点——跨通道前缀互斥、祖先畅通（现场非目录挡位）、保留源保护
   （node.log 的 staging source 不落 job_dir 落点集，remote 落点与之同位或位于其
   下会抹掉它）——纯路径数学零写入；冲突即整个结果干净 failed（零字节应用、
   Worker staging key 保留）。预检**收集全部冲突**（不短路）：`LandingConflict.names`
   是全集，失败 finish 只挂闸安全且未参与冲突的归档 moves（node.log 等观测
   parity）——冲突 move 不挂，其 staging source 不再被抢先消耗，同名观测 move
   随之能真正落盘而不是被误当事务重放跳过（codex #774 P2；只带第一对会让兄弟
   落点/第二对冲突漏摘，#774 对抗复审 P2）；
2. **全域读视图**（`agent_control/completion_view.py`）：staging 视图是私有
   scratch，链接对归档垃圾形状（同名目录、文件祖先、symlink）与源消失 TOCTOU
   全域——overwrite 遍清挡位垃圾（预检保证删不到暂存源），第一遍遇挡位跳过按
   未产出判 missing，永不炸异常；
3. **闸内兜底**（`executors/_lease_finish_promotion.py`）：预检无锁，盖不住跨
   节点 finish 之间现场变坏的残余竞态——`staged_file_moves` 提升失败经 guard
   整体回滚后 completed 转 failed 照常提交，lease 不再被异常回滚毒化成重试循环。

## 3. 对抗审查 checklist

本协议经多轮对抗审查收敛；把发现过真实问题的三个切面固化为 checklist。审查
任何新增/修改执行态写面或批路径的 PR 时逐条过：

### 3.1 并发串行化切面

- [ ] 每个 mutation 是否在共享锁域内取锁（`job-mutation:<job_id>`），且是事务
      首句或满足锁序的位置？有没有绕过 `lease_guarded_mutation` /
      `lock_job_mutation_and_read_generation` 直接改 `job_nodes`/`jobs` 的新路径？
- [ ] 新路径的取锁顺序是否与全局锁序一致（池级 → job-mutation → 行锁）？
      有没有「先拿行锁再取 advisory 锁」的 AB-BA（历史教训：请求行 FOR UPDATE
      必须先移到 advisory 锁之后）？
- [ ] 批路径是否只有一个全序？新增批是否沿用
      `(hashtext('agent-ws:' || workspace_id)::int, job_id)`，包括不取 ws 锁的
      code 候选？有没有第二条排序规则（纯 job_id、按扫描返回序）混入？
- [ ] advisory xact 锁语义是否被正确对待：savepoint 回滚不释放锁、xact 锁不可
      跨连接重入（持锁事务内不要另起短事务取同一把锁，会自锁）？
- [ ] 无锁扫描 + 锁内复查的两段式里，复查是否覆盖了扫描之后状态可能改变的全部
      字段（状态、代次、lease 活性、心跳）？

### 3.2 协议覆盖完备性切面

- [ ] 机器兜底先行：`scripts/architecture/execution_write_surfaces.py`
      （EXEC-GENERATION-002）钉住执行态/产物字节/清单行三类写面全集
      （`config/architecture/execution-write-surfaces.json`），注册表外的
      写面 CI 直接拒绝——本切面的人工部分只审「新登记的写面是否真走了 CAS
      或共享 helper」：重点扫边缘路径：sweeper、审批、enqueue、
      not_applicable 批写、分片 fan-out、失败记录——历史上每一个都曾是漏网面。
- [ ] 降级臂是否会产生缓存污染？读失败 / 部分成功时，评估结果是否被当成确定性
      结论缓存？
- [ ] 「取消 vs requeue」的方向是否选对？旧代次请求取消后由新代次调度重新入队；
      requeue 旧请求 = 双跑。
- [ ] 「跳过 vs 翻转」的方向是否选对？过期写面跳过翻转后，节点必须能被下一轮
      调度重新拾起（保持 pending + bump 改了扫描 mark），不能停在无人认领的
      中间态。
- [ ] 盖戳义务是否完整？翻 running / park 等新状态时必须盖当前代次戳，否则下游
      代次闸门（如孤儿恢复只认现值戳）会把该行永久卡住。
- [ ] 产物字节写面是否纳入闸？凡在 finish CAS 之前/之外落地的字节或清单写口
      （promote、镜像上传、fan-out 物化）都要过 `lease_artifact_write_current`
      或等价的锁内复查。
- [ ] 重置/重建 `job_nodes` 的 mutation 是否同事务了结受影响的 queued
      请求？节点级重置走 `_cancel_queued_sql`；节点集合整体重建（clean
      upgrade）走 `cancel_queued_requests_for_job`（按节点过滤会漏掉已不
      在新定义里的旧节点）。job/workspace 删除走 FK cascade，无需取消。
- [ ] 重置集 ≡ 暂存集？凡把节点翻回 pending/stale 的操作，失效的产物
      集合必须与重置的节点集合完全相等：`stage_outputs` 不做任何图遍历
      （无下游扩展、无闭包过滤），只消费调用方传入的权威集合；调用方用
      计算重置集的同一个变量喂给它。执行范围过滤器（如 run-to 的
      `closure`）不得参与暂存判定——闭包外的隐式消费者同样在重置集里。
- [ ] 旧产物名的存亡是否由按名闭包唯一判定？跨 revision 比较（upgrade）
      里「旧 output − 新 output − 新 input」是唯一的死活判据；任何按节点
      的 output 枚举（无论新旧定义）都不得再决定名的存亡——被删生产者的
      产物若已转移为新输入，枚举会把种子误删。被删节点只清 run history
      （`include_outputs=False`）。
- [ ] 状态相关的决策集合是否在 mutation 锁内重算？锁外读数到取锁之间，
      目标可能被 claim/完成/重置（所有写入方持同一把 job-mutation 锁，
      锁内读数才是最终态）。集合与状态无关（纯图闭包）则无此面；一旦
      依赖状态过滤（如「非 completed」），必须锁内重读并让暂存/清单
      删除/节点更新由同一份当前集合驱动。

### 3.3 纯逻辑正确性切面

- [ ] 降级语义是否保守方向正确：不可证明即排除/重跑（读失败、证据缺失），
      而不是乐观放行？误报（多跑）可接受，漏报（旧产物冒充新执行）不可接受。
- [ ] 全局比较是否会误伤局部事实（审批门教训：bump 是全局的，重置是闭包内的，
      「行戳 == 现值」的全局比较会把未重置行误判过期）？每个 CAS 比较的两端是否
      确实同域？
- [ ] 失败补偿是否成对出现且幂等：暂存↔回滚、备份↔恢复、登记↔删除；补偿失败
      是否 per-item 兜住而不中断其余补偿？
- [ ] 清理动作的删除前提是否成立（§2.8 补偿资源表）：被删资源防范的中间态此刻
      是否已确认解除？补偿自身失败（恢复 copy 失败、回滚遇目录形态）时，最后
      恢复源是否被保留且有可追寻的日志指针？
- [ ] 新下游/上游判据是否进了 `workflow_consumption` 的统一邻接表
      （显式边 ∪ 隐式消费/生产边，`dependency_children` /
      `dependency_parents`），而不是手补列表？下游（重置闭包）与上游
      （failed-upstream 守卫、run-to closure、rework 目标、until_node
      允许集）必须用同一张合并图；调度就绪的隐式生产者完成屏障与
      隐式边共用 `artifact_producers` 同一索引。

## 4. 已知残余面与后续方向

协议刻意留下的残余面（审查时不要再当新发现报，但也不许扩大）：

1. **执行进程沙箱内直写 job_dir** 只靠 lease 生命周期约束：运行中的沙箱进程
   对 job_dir 的写入不经过代次闸；reset 拦在 claim/finish 两面，进程内文件的
   旧字节由「新代次生产者重跑覆盖 + 暂存/清理」兜底。
2. **promote 的 DB 连接死亡窗口**：备份/copy/权威复查/失败恢复都在按 key
   advisory 锁内（§2.8），但 DB 连接中途死亡（含 commit 时刻）时锁随会话
   释放，失败恢复降级为无串行的 best-effort（`restore_authority_backups`，
   **单发**——锁已不在，退避只放大本窗口）。
   commit 歧义的另一半（ack 丢失、服务端实际已提交）下恢复会把旧字节盖回、
   与已提交的新清单行错位——选边偏向远更常见的 rollback half（连接死于
   commit 到达前、序列化失败、死锁都是回滚），不再收窄；恢复最终失败的
   备份对象保留（最后恢复源，ERROR 日志带 key；`s3_jobs_gc` 豁免
   `/.rollback/` 段），无备份时的孤儿 authority 对象由 bucket lifecycle 兜底。
3. **闸内文件提升的提交前窗口**：`finish_lease` 与 `register_rows_guarded` 的
   staged 文件提升都在代次 CAS 之后、事务提交之前完成（本地 rename，毫秒级）；
   提升成功后同事务后续 SQL 失败的崩溃窗口会留下「当前代次自身产物」的已落盘
   文件，lease 仍 active、重试自然覆盖——不跨代次污染，不再收窄。finish 批
   事务（`finish_many`）整批回滚重放由「source 缺席 + target 在场 = 已提升」
   的幂等跳过兜住（`promote_file_moves_guarded`），瞬时 DB 冲突不会被放大成
   确定性 500。
4. **ready 前输入恢复（hydration）尚无代次夹逼**：清单驱动的输入恢复与
   消费关系索引（含 `edge.condition.artifact` 等隐式消费面）的统一建模是后续
   artifact-dependency-model 层的内容。
5. **upgrade inherit 模式**（保留未变节点产物）与发布/skill 锁域接入同一
   协议是后续 upgrade-inherit 层的内容。

#759 五面对抗自审（2026-09）登记、经 triage 暂不修的残余项（多为
pre-existing 或需后续层设计；评审时按现状接受，不许扩大）：

6. **eviction 淘汰输入文件后 targeted rerun 永不 ready**：`restore` 挂在
   claim 后的 `execute()`，而 `_inputs_exist` 在 ready 评估就把它挡死——
   恢复路径逻辑上不可达，job 静默卡 queued。修复需 hydration 下沉到
   ready/dispatch 评估前（归 artifact-dependency-model 层）。
7. **not_applicable 化已失效生产者困死纯隐式消费者**：rerun 重置并失效
   产物后，分支条件把生产者翻 not_applicable，文件永不再生、隐式消费者
   永久 pending。修复需 ready-gate 沿合并邻接传播 not_applicable（同上层）。
8. **审批 approve 产物文件事务前写**：并发决策下败者的文件可能覆写胜者
   的上传内容（窗口窄）；round_no 锁外计数可重号。rework 的 feedback
   已在锁内紧随暂存之后写入（自审修复：提交后写有 stale/missing-read
   窗口，事务前写会被暂存扫走），回滚残留的新 note 由下轮覆盖。
9. **单 claim 多候选单事务的 advisory 锁累积**：§2.5 的全序论证只覆盖
   批路径；单 claim 面靠 40P01 一次重试 + deadlock_timeout 缓解。
10. **`mark_nodes_not_applicable_many` 翻 not_applicable 不盖代次戳**：
    当前无任何按戳消费方，登记为不对称点；后续若按戳判别归属须先补戳。
11. **run-to 两臂下游语义差**：with-start 把目标下游翻 stale，without-start
    只重置 closure ∩ 非 completed（文档化差异，刻意性待产品确认）。
12. **legacy 无快照 job 的 clean upgrade**：旧定义不可知，本地产物文件
    无法暂存（清单行/对象仍失效），残留文件可能解锁无生产者 input。
13. **sweeper 遗留**：lease 行消失后 claimed/reporting 请求无归属
    （`lease is None: continue`）；agent sweep requeue 守卫含 failed 可
    复活聚合判死的节点；unclaimable sweep 固定头 256 窗口尾部饿死。
14. **retention**：keyset 游标无 skew 重叠窗（近同时提交的行可永久漏删）；
    retention 删请求行与 reaper 删 bundle 文件无顺序保证（极端停摆下
    bundle 文件泄漏）。
15. **批/单发对 start 节点的拒绝 reason_code 不一致**；run-to 两臂与
    upgrade 提交后未 `notify_schedulable_work`（有周期扫描兜底则为延迟
    差异）；run-to 不清 `node_runs.run_dir/session_dir`（日志路径 404）。
16. **迟到旧代次 Worker 结果的登记先于 finish 代次 CAS**（§4.2 的交互
    放大）：cleanup 的「键复现 = 新 attempt」启发式会把旧代次迟到登记
    误判为新产物放过，陈旧行/对象在新一代重跑完成前可被服务。
17. **legacy 无锁直写臂与 guarded promote 恢复臂的竞态**：无 lease 的
    upload（reconciler `reupload_missing`、approval 附件上传）直写
    authority key、不进 `artifact-authority` 锁域——与 guarded promote
    的闸拒/失败恢复交错时，恢复可能把旧字节盖回直写臂刚写入的对象，
    而清单行指向直写字节（hash/size 错位潜伏：读路径 local-first，本地
    副本与行同 hash；eviction 淘汰本地后 S3 回落才暴露）。收敛论证：
    当前代次 finish 的合法 promote 会覆盖收敛；残留条件是「当前代次
    上传持久失败 + reconciler 已成功」的组合。后续方向：reconciler 加
    active-lease 复查（`_job_still_evictable` 同款）或 legacy 臂进同一
    按 key 锁域（锁-only，不过闸）。

后续方向：评估 immutable/versioned authority key + manifest 原子切换（#759
复审增补的长期项）；`.result-staging-*` / `.promote-rollback-*` 的进程崩溃残留
目前无 reaper（纯磁盘泄漏，消费者按名读取不受影响），值得一个 sweeper 或启动
清理的后续项。

## 5. 验证与测试手法

- 交错测试矩阵：`tests/db/test_execution_generation_races.py`（claim/enqueue/
  approval/fail/finish 案）与 `tests/db/test_sweeper_generation_races.py`、
  `tests/db/test_enqueue_generation_races.py`。手法：两条连接，主线程手工控事务
  节奏，用 `pg_locks` 观测同步点（不裸 sleep），连接经 TIMED_DATABASE_URL 带
  `deadlock_timeout=50ms` + `lock_timeout=5s`（有环必现、意外等待有界），
  `thread.join(timeout)` 后断言线程已死防假绿；每案断言「双方合理收尾 + 最终
  状态 == 某种合法串行序的结果」。批序回归案带突变自检：在旧的纯 job_id 序下
  本案必死锁。
- 产物写面闸：`tests/db/test_generation_write_gates.py`（字节闸交错案）与
  `tests/db/test_completion_generation_gates.py`（归档 staging + finish 闸内
  提升）、`tests/services/test_agent_completion_remote.py` 系列。
- 协议成员名单与证据：`config/architecture/architecture-invariants.yaml` 的
  EXEC-GENERATION-001 条目（正式表述 + evidence 列表）。

## 6. 相关文档

- [workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)：
  架构承诺的反向审计证据矩阵（EXEC-GENERATION-001 行）。
- [node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md)：
  节点 SDK 与 Worker 执行模型（lease、job_dir、产物通道的底层设计）。
- [backend.md](backend.md)：后端服务总览（lease 申请、对象存储、配置治理）。
- AGENTS.md §6 Boundary Rules：EXEC-ARTIFACT-STORE-001（产物权威副本在对象
  存储）、EXEC-APPROVAL-001（审批门语义）等关联红线的摘要。
