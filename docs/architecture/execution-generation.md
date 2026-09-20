# 执行代次协议（EXEC-GENERATION-001）与三平面一致性

本文是执行代次协议（execution generation protocol）的现行架构文档：它描述
workflow 升级/重跑与并发执行之间的串行化机制、跨三个数据平面的一致性论证，
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
   新现场；或按已被重置抹去的旧 revision / 旧实现身份继续执行。
2. **重跑与在途 lease 交错**：迟到 finish、迟到 failure 记录、心跳过期清扫、
   孤儿 running 行恢复等边缘写路径各自为政，没有一个统一的「这次写属于哪一轮
   执行」判定；旧轮次的清扫甚至会把新轮次重建的 pending 行翻成 failed。
3. **批路径死锁**：批操作（finish_many、批 claim、sweep）与 mutation 侧以不同
   顺序访问同一批 job 的行锁与 advisory 锁，交叉即 AB-BA 死锁（40P01）。

协议的目标不是消灭并发，而是给每个执行态写面一个共同的**代次（epoch）判据**：
突变把 job 推进到新一代，一切持旧代次的写面在新一代面前自动放弃或降级为
收尾-only。

## 2. 协议定义（EXEC-GENERATION-001）

### 2.1 代次列（schema v86）

`jobs.execution_generation` 是单调递增的执行代次权威源，镜像到各写面自有
的行上（DDL 在 `server/app/db/migrations/execution_generation.py`，apply-fn
风格，幂等）：

| 表 | 盖戳时机 | CAS 语义 |
| --- | --- | --- |
| `jobs` | 突变事务内 +1（唯一 bump 源） | 所有比较的现值基准 |
| `job_nodes` | 重置/重建时盖新戳；park approval 盖当前戳 | recover / not_applicable 批写按戳判别归属 |
| `node_runs` | claim 时盖戳 | finish 的 CAS 比较 lease 戳而非本行 |
| `executor_leases` | claim 时盖戳 | finish / 产物写闸比较 lease 戳与 jobs 现值 |
| `agent_execution_requests` | enqueue 时盖戳 | claim / sweep 比较请求戳与 jobs 现值 |

`default 0` 让 v86 之前的存量行天然一致：在途旧 lease 与其 job 同为代次 0，
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
| run-to（无起始节点） | `apply_run_to` → `set_run_to_control(bump_generation=True)` | run-to-with-start 同事务已由 `mark_nodes_for_rerun` bump，这里不再 bump——整事务恰好一次 |
| workflow upgrade（clean / inherit） | `upgrade_job_workflow_inherit`（`server/app/jobs/workflow_upgrade_mutation_inherit.py`） | fold 进 revision 切换的 jobs UPDATE；保留的 inherit 行不动，新增/重置行盖新戳 |

不 bump 的突变：resume、delete、approval park（park 只盖当前戳，自身不推进
代次）。未被重置的节点行永不改戳——这是审批门语义成立的前提（见 2.4）。

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
| 2 | `job-mutation:<job_id>` | mutation 侧首句；所有执行态写面与产物写闸在池锁之后（或无池锁直接）取 |
| 3 | `implementation-publication:<workspace_id>` | upgrade guard 事务（无条件，先于 active revision 重读）；发布侧共享（见 2.6） |
| 4 | `skill-lock`（全局单文档域） | skill 锁文档写与 upgrade 重验（见 2.6） |
| 5（最内） | 行锁（`FOR UPDATE` 等） | 各写面自身 |

无环论证：mutation 侧不取池锁，发布侧不碰 job 行也不取 job-mutation 锁，
跨层只有单向边。enqueue 不持池锁、直接取 job-mutation，环保持无环。

**批序全序**：每个跨 job 的批（finish_many、try_claim_many、expire、recover、
agent sweep、两个 queued-request sweep、批 claim 的每个候选——code 也包括，
借 ws 锁键纯当排序位，code 本不取 agent-ws 锁）一律按全库唯一序
`(hashtext('agent-ws:' || workspace_id)::int, job_id)` 遍历；jobs 行已消失的
孤儿项用哨兵 `-(2**31)` 定位（`_ORPHAN_WS_LOCK_KEY`），job_id 收尾决胜。单一
全序保证任意两批不会以相反顺序走同一对 job。

### 2.6 升级侧的发布锁域与 RMW 保护

workflow upgrade 的单次应用尝试（`apply_upgrade_once`，
`server/app/services/job_workflow_upgrade_apply.py`）按「先全部校验备妥、再统一
应用」组织：inherit 继承集在事务外规划（纯函数），guard 事务内的首步**无条件**
取 `implementation-publication:<ws>` advisory 锁，然后锁下重读 active revision
（`assert_context_revision_current`）——与 plan 之间已完成的发布即 TOCTOU，
抛 `ActiveRevisionChangedError`，整个尝试作废并由 service 层整体重试一次（重解
context + 重 plan + 重进事务，禁止半应用状态）。有继承候选时再取全局
`skill-lock` 域做实现身份重验（漂移节点放弃继承、降级重跑，传播面由收敛层
接管）。

两个发布锁域的成员：

- `implementation-publication:<ws>`：`versioned_entities` 的 Agent / node_code
  发布、回滚、归档（`server/app/jobs/queries/upgrade_impl_identity.py`）、
  active workflow revision 发布
  （`server/app/jobs/queries/workflow_revision_projection.py`）、runtime-only
  原地编辑（`server/app/services/workflow_revision_runtime.py`）。
- `skill-lock`（全局）：`SkillLockStore.put_lock` 的全部写（dispatch 首次 pin、
  `make skills-lock` 重锁）与 upgrade plan 阶段的锁内读
  （`server/app/services/skill_lock_store.py`）。dispatch 热路径的解析读不进
  本域。

skill 内容身份判定（`server/app/services/job_workflow_upgrade_skill.py`）是安全
敏感读，纪律为：直读 DB 锁文档（`read_skill_lock` 经 `SkillLockStore` 绕开 5s
doc cache）；`latest` 绑定**恒定排除**（跟随 live HEAD，不做 live rev-parse 就
证明不了任何东西）；pinned ref 与锁文档 `refs[ref]` 比较才可继承；锁内无 ref /
无锁文档 = 不可证明 → 排除；upgrade **永不触发首次 pin**、不跑 git 子进程，
事务回滚不留 skill 面副作用。

### 2.7 重跑闭包：三通道传播

「哪些节点必须跟着重跑」由种子 + 闭包回答
（`server/app/services/job_workflow_upgrade_propagation.py`）：种子（S1–S5：定义、
配置、入边、实现身份、可达性）只做局部判定，传播闭包三通道——

- 通道 A：新图显式边的传递下游；
- 通道 C：隐式 input 消费边（`server/app/workflows/workflow_consumption.py`：
  loader 不要求 input 的生产者有显式边，调度器靠输入文件出现解锁，因此 output
  名 → 生产者索引构成 producer→consumer 隐式边，与显式边合并为一张邻接表，
  环内互染、保守不漏）；
- 通道 B：与重置面共享输出名（含 RMW）的候选一起重跑（fixpoint）。

任何新重跑原因只要进种子集就自动获得全下游传播——这是「新判定源不可能忘了
传播」的结构保证，也是代次协议的前提：闭包划错，CAS 护住的现场本身就是错的。

## 3. 三平面一致性论证

Job 产物与执行状态分布在三个平面，代次协议对每个平面各有一道闸：

1. **DB 状态平面**（`jobs` / `job_nodes` / `node_runs` / `executor_leases` /
   `agent_execution_requests`）：上节全部 CAS 面。核心性质：重置在某代次重建
   `job_nodes` 后，持旧代次的写面要么不写（claim/enqueue/fail/park/fan-out/
   not_applicable），要么只收尾不翻转（finish/sweep），要么按突变侧语义取消
   自身（stale 请求）。
2. **本地 job_dir 文件平面**：本地目录只是执行暂存与可淘汰缓存
   （EXEC-ARTIFACT-STORE-001）。突变侧在锁内暂存/移除重置面产物（任何失败先
   回滚暂存再上抛）；ready 前的输入补水
   （`server/app/workflow_worker/input_hydration.py`）**刻意不取** job-mutation
   锁（对象存储下载可能数秒，不能挡住每个 rerun/upgrade），改为在 manifest 读
   之前与所有恢复写之后各读一次代次做夹逼：代次变了 = 恢复的字节可能来自已被
   作废的清单行，恰删本轮恢复的文件并**不缓存**地推迟到下一轮。降级臂（manifest
   读失败 / 代次预读失败）返回 None 同样不缓存——清单是权威副本，读失败绝不能
   退化成「只看本地文件」的粘性误判。残余窗口（突变在通过的复查之后提交）由三层
   兜底：本轮候选带旧代次会被 claim CAS 拒；下一轮重评估时该名字的清单行已消失、
   不再恢复；`unprotected_input_names` 保证无保证先行生产者的输入名不被误用。
3. **对象存储清单平面**（`job_artifacts` 清单 + 对象字节）：清单行是权威副本
   （EXEC-ARTIFACT-STORE-001）。突变侧在同一事务删除被重置产物的清单行（rerun
   的 `mark_nodes_for_rerun` 删 staged 行；upgrade clean 语义分支做全量清单
   清理，`keep_input_names` 保护 RMW 启动名与外部输入）。写面闸是
   `lease_artifact_write_current`（`server/app/executors/_lease_write_gate.py`：
   job-mutation 锁下复查 lease 仍 active、心跳未过期、落戳代次 == jobs 现值），
   两个在 finish CAS **之前**落地的字节写口都是协议成员，且（#759 复审 P1-B
   起）共用同一个 promote primitive
   `executors/_artifact_promotion.promote_to_authority_guarded`：字节先落
   per-execution/lease 的 staging key（**绝不直写 authority key**），既有
   authority 对象先 server-side 备份到回滚 key，锁外 copy
   staging→authority，权威复查在 `register_rows_guarded`——**一个事务**内
   取锁、复查、（远端臂）把 staged 文件落盘、upsert 清单行。与突变侧只有
   两种序：登记先提交（随后被突变当作重置面删除），或突变先提交（闸拒绝
   登记）。被拒的 promote 用回滚备份恢复已完成的 authority-key copy，不
   落盘、不复活清单行、不让保留行指向污染字节，结果提交面呈现既有 409
   语义。
   - Worker 回传 promote：`remote_artifact_promote.promote_all` 在任何字节
     copy 前先做无锁预检，随后走上述共享序列（execution_id 为 staging/回滚
     key 的 execution 维度）。
   - 本地 code 执行的 D12 镜像上传：`artifact_mirror.upload_produced_artifacts`
     携带 context 的 lease_id，入口闸关闭即整批跳过；未跳过则由
     `JobArtifactObjectStore.upload` 的 lease 臂把字节写到 per-lease staging
     key（lease_id 即 execution 维度）后走同一 primitive——循环中途落地的
     reset 既登记不进去，也不会让 authority 对象被旧代次字节覆盖（闸拒时按
     备份恢复，staging 残留逐结局清理）。

   因为行删除与行登记在同一把锁下互斥，清单平面不存在「旧代次行复活」的交错；
   字节面由「staging 先行 + 闸拒回滚」保证不存在「保留行指向污染字节」的交错。

## 4. 对抗审查 checklist

本协议经多轮对抗审查收敛；把发现过真实问题的三个切面固化为 checklist。审查
任何新增/修改执行态写面、批路径或产物写面的 PR 时逐条过：

### 4.1 并发串行化切面

- [ ] 每个 mutation 是否在共享锁域内取锁（`job-mutation:<job_id>`），且是事务
      首句或满足锁序的位置？有没有绕过 `lease_guarded_mutation` /
      `lock_job_mutation_and_read_generation` 直接改 `job_nodes`/`jobs` 的新路径？
- [ ] 新路径的取锁顺序是否与全局锁序一致（池级 → job-mutation →
      implementation-publication → skill-lock → 行锁）？有没有「先拿行锁再取
      advisory 锁」的 AB-BA（历史教训：请求行 FOR UPDATE 必须先移到 advisory
      锁之后）？
- [ ] 批路径是否只有一个全序？新增批是否沿用
      `(hashtext('agent-ws:' || workspace_id)::int, job_id)`，包括不取 ws 锁的
      code 候选？有没有第二条排序规则（纯 job_id、按扫描返回序）混入？
- [ ] advisory xact 锁语义是否被正确对待：savepoint 回滚不释放锁、xact 锁不可
      跨连接重入（持锁事务内不要另起短事务取同一把锁，会自锁）？
- [ ] 无锁扫描 + 锁内复查的两段式里，复查是否覆盖了扫描之后状态可能改变的全部
      字段（状态、代次、lease 活性、心跳）？

### 4.2 协议覆盖完备性切面

- [ ] 新写面（任何写 `job_nodes`/`jobs`/`agent_execution_requests`/清单行的路径）
      是否走了 CAS 或共享 helper？重点扫边缘路径：sweeper、审批、enqueue、
      not_applicable 批写、分片 fan-out、失败记录——历史上每一个都曾是漏网面。
- [ ] 降级臂是否会产生缓存污染？读失败 / 部分成功时，评估结果是否被当成确定性
      结论缓存（ hydration 纪律：manifest 是权威副本，读失败 = 推迟且不缓存，
      绝不退化为「只看本地文件」）？
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

### 4.3 纯逻辑正确性切面

- [ ] 降级语义是否保守方向正确：不可证明即排除/重跑（skill 锁内无 ref、读失败），
      而不是乐观放行？误报（多跑）可接受，漏报（旧产物冒充新实现）不可接受。
- [ ] 「跟随 HEAD」类动态引用是否恒定排除出可继承/可缓存面（latest 绑定零 git
      I/O 下不可证明）？安全敏感读是否绕开了 TTL 缓存直读权威源？
- [ ] 全局比较是否会误伤局部事实（审批门教训：bump 是全局的，重置是闭包内的，
      「行戳 == 现值」的全局比较会把未重置行误判过期）？每个 CAS 比较的两端是否
      确实同域？
- [ ] 失败补偿是否成对出现且幂等：暂存↔回滚、备份↔恢复、登记↔删除；补偿失败
      是否 per-item 兜住而不中断其余补偿？
- [ ] 新判定源是否进了种子集从而自动获得传播闭包，而不是手补下游列表？

## 5. 已知残余面与后续方向

协议刻意留下的残余面（审查时不要再当新发现报，但也不许扩大）：

1. **执行进程沙箱内直写 job_dir** 只靠 lease 生命周期约束：运行中的沙箱进程
   对 job_dir 的写入不经过代次闸；reset 拦在 claim/finish/产物登记三面，进程
   内文件的旧字节由「新代次生产者重跑覆盖 + 暂存/清理」兜底。
2. **promote 的锁外 authority-key copy**：字节 copy 在锁外执行（可中断的大
   字节量不该持锁），靠回滚备份兜底（`restore_authority_backups`）；权威性
   部分（落盘 + 清单行）在锁内单事务完成。本地上传（lease 臂）与远端
   promote 共用同一 primitive（`executors/_artifact_promotion.py`），两侧
   残余面相同；无备份时的孤儿 authority 对象由 bucket lifecycle 兜底。
3. **AgentCompletionHandler.finish 的 D12 镜像调用未加闸**：
   `server/app/agent_control/completion.py` 的 `upload_produced_artifacts` 调用
   不传 lease_id（本地 code 执行路径传）。agent 本地产物镜像因此可能把旧代次
   字节登记进清单；`job_nodes` 面由 finish CAS 护住，但该写口值得后续加闸。
4. **hydration 残余窗口**：突变可在通过的代次复查之后提交，留下一个毫秒级
   （单事务 staging→commit 跨度、按文件计）的旧字节文件；三层兜底论证见
   `input_hydration.py` 模块 docstring。

后续方向：给 agent 完成路径的镜像上传补 lease_id 闸；评估 hydration 是否可在
不阻塞突变的前提下收掉残余窗口（如按名字级的版本化暂存目录）。

## 6. 验证与测试手法

- 交错测试矩阵：`tests/db/test_execution_generation_races.py`（claim/enqueue/
  approval/fail/finish 九案）与 `tests/db/test_sweeper_generation_races.py`、
  `tests/db/test_enqueue_generation_races.py`、
  `tests/db/test_upgrade_lock_domains.py`、
  `tests/db/test_generation_write_gates.py`。手法：两条连接，主线程手工控事务
  节奏，用 `pg_locks` 观测同步点（不裸 sleep），连接经 TIMED_DATABASE_URL 带
  `deadlock_timeout=50ms` + `lock_timeout=5s`（有环必现、意外等待有界），
  `thread.join(timeout)` 后断言线程已死防假绿；每案断言「双方合理收尾 + 最终
  状态 == 某种合法串行序的结果」。批序回归案带突变自检：在旧的纯 job_id 序下
  本案必死锁。
- 协议成员名单与证据：`config/architecture/architecture-invariants.yaml` 的
  EXEC-GENERATION-001 条目（正式表述 + evidence 列表）。

## 7. 相关文档

- [workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)：
  架构承诺的反向审计证据矩阵（EXEC-GENERATION-001 行）。
- [node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md)：
  节点 SDK 与 Worker 执行模型（lease、job_dir、产物通道的底层设计）。
- [backend.md](backend.md)：后端服务总览（lease 申请、对象存储、配置治理）。
- AGENTS.md §6 Boundary Rules：EXEC-ARTIFACT-STORE-001（产物权威副本在对象
  存储）、EXEC-APPROVAL-001（审批门语义）等关联红线的摘要。
