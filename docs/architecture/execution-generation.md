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
| workflow upgrade（clean） | `upgrade_job_workflow`（`server/app/jobs/workflow_upgrade_mutation.py`） | fold 进 revision 切换的 jobs UPDATE；bump 先于节点行重建，重建行盖新戳；节点集合整体替换，同事务按 job 作用域了结全部 queued 请求（`cancel_queued_requests_for_job`）——遗留行无人 claim 时会把 `has_active_request` 的闸门外重派无限期挡住；新旧定义可执行节点之并的全部产物暂存失效、清单行全删 |

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
| finish（含分片臂） | `finish_lease`（`server/app/executors/_lease_lifecycle.py`） | lease 释放与 `node_runs` 终态照常收尾，但跳过 `job_nodes` 翻转、`sync_job_status` 与 until_node 暂停 |
| fail_without_lease | `server/app/executors/_lease_config_failure.py` | 整个配置失败记录跳过，节点保持 pending 等下一轮 |
| approval park | `park_awaiting_approval`（`server/app/executors/_lease_approval.py`） | 评估代次过期则跳过 park；成功的 park 给 gate 行盖当前戳 |
| approval 决策 | `approve_gate_atomic` / `reject_gate_atomic`（`server/app/jobs/queries/approval_decisions.py`） | 锁下状态守卫（`ApprovalGateConflict`）；**刻意不做代次比较**，见 2.4 |
| lease 过期清扫 | `expire_stale_leases`（`server/app/executors/_lease_lifecycle.py` 等） | 过期代次的回写跳过 |
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
| 2 | `job-mutation:<job_id>` | mutation 侧首句；所有执行态写面在池锁之后（或无池锁直接）取 |
| 3（最内） | 行锁（`FOR UPDATE` 等） | 各写面自身 |

无环论证：mutation 侧不取池锁，跨层只有单向边。enqueue 不持池锁、直接取
job-mutation，环保持无环。

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

产物字节写面本层按现状登记（`remote_artifact_promote` /
`remote_artifact_support` / `job_artifact_objects`），尚未过代次闸——收口进
共享 promotion primitive 是后续 artifact-commit-protocol 层的内容（见 §4）。

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
- [ ] 重置/重建 `job_nodes` 的 mutation 是否同事务了结受影响的 queued
      请求？节点级重置走 `_cancel_queued_sql`；节点集合整体重建（clean
      upgrade）走 `cancel_queued_requests_for_job`（按节点过滤会漏掉已不
      在新定义里的旧节点）。job/workspace 删除走 FK cascade，无需取消。
- [ ] 重置集 ≡ 暂存集？凡把节点翻回 pending/stale 的操作，失效的产物
      集合必须与重置的节点集合完全相等：`stage_outputs` 不做任何图遍历
      （无下游扩展、无闭包过滤），只消费调用方传入的权威集合；调用方用
      计算重置集的同一个变量喂给它。执行范围过滤器（如 run-to 的
      `closure`）不得参与暂存判定——闭包外的隐式消费者同样在重置集里。
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
- [ ] 新下游判据是否进了 `workflow_consumption` 的统一邻接表（显式边 ∪ 隐式
      消费边），而不是手补下游列表？

## 4. 已知残余面与后续方向

协议刻意留下的残余面（审查时不要再当新发现报，但也不许扩大）：

1. **执行进程沙箱内直写 job_dir** 只靠 lease 生命周期约束：运行中的沙箱进程
   对 job_dir 的写入不经过代次闸；reset 拦在 claim/finish 两面，进程内文件的
   旧字节由「新代次生产者重跑覆盖 + 暂存/清理」兜底。
2. **产物字节写面未过代次闸**：Worker 回传 promote 与本地镜像上传仍直写
   authority key（注册表 `artifact_byte_write_sites` 按现状登记）。收口为
   「staging + 锁内闸 + 失败回滚」的共享 promotion primitive 是后续
   artifact-commit-protocol 层的内容。
3. **ready 前输入恢复（hydration）尚无代次夹逼**：清单驱动的输入恢复与
   消费关系索引（含 `edge.condition.artifact` 等隐式消费面）的统一建模是后续
   artifact-dependency-model 层的内容。
4. **upgrade inherit 模式**（保留未变节点产物）与发布/skill 锁域接入同一
   协议是后续 upgrade-inherit 层的内容。

## 5. 验证与测试手法

- 交错测试矩阵：`tests/db/test_execution_generation_races.py`（claim/enqueue/
  approval/fail/finish 案）与 `tests/db/test_sweeper_generation_races.py`、
  `tests/db/test_enqueue_generation_races.py`。手法：两条连接，主线程手工控事务
  节奏，用 `pg_locks` 观测同步点（不裸 sleep），连接经 TIMED_DATABASE_URL 带
  `deadlock_timeout=50ms` + `lock_timeout=5s`（有环必现、意外等待有界），
  `thread.join(timeout)` 后断言线程已死防假绿；每案断言「双方合理收尾 + 最终
  状态 == 某种合法串行序的结果」。批序回归案带突变自检：在旧的纯 job_id 序下
  本案必死锁。
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
