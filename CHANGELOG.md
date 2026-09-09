# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) once 1.0.0 is released.

## [Unreleased]

## [0.7.7] - 未发布

### Fixed
- Worker 双 attempt 竞态修复（issue #564）：worker 过载时批量心跳被饿死，
  Host 误判租约过期重排队，同一 worker 立刻重新 claim 同一 execution_id
  而旧 attempt 线程仍存活；旧 attempt 的丢弃收尾仅检查 pending-upload
  marker 就 rmtree 整个 execution_dir，删掉新 attempt 刚重建、正在使用
  的目录，新 attempt 写 prompt.md 时 FileNotFoundError。两层修法：
  - **归属标记**：prepare 重建目录后立刻写入 `execution_owner.json`
    （claim 的 lease_id，agent 与 code 两条路径同）；丢弃收尾只在标记
    仍指向自己的 lease 时才 rmtree——标记易主（目录已被新 attempt 占
    用）或缺失/损坏一律跳过删除，证明不了归属的孤儿目录归 startup
    clean_work_root / stale sweeper 清理（与 #203 的 pending marker
    先例同构）。
  - **per-execution 互斥锁**：`run_execution` 全程持有按 execution_id
    引用计数的进程内锁（`worker/execution/ownership.py`），同一
    execution_id 在本进程内任意时刻只有一个 attempt——旧 attempt 的
    收尾与新 attempt 的 prepare 串行化，时序窗口整体消除。等锁是有界
    等待（`MUTEX_WAIT_BOUND_SECONDS` = 60s，按 90s 租约 TTL 基线留出
    两个批量心跳拍，够旧 attempt 收到 409 并完成收尾）：超时说明心跳
    面仍瘫痪、本 claim 的 lease 在 Host 侧已死或濒死，放弃本次 claim
    （不 prepare、不上报、不启动心跳），租约过期后由 Host 在 worker
    恢复健康时重排。
- Host 侧心跳饿死止血（issue #566 一期）：worker 过载时进程内心跳
  daemon 线程抢不到 GIL 被饿死，心跳静默超过租约 TTL，而控制面（claim
  轮询）仍在正常触活 `agent_workers.last_seen_at`——旧 sweep 把「执行面
  心跳饿死」误判为「worker 死亡」，同一 sweep 批量过期 → 重排队 → 立即
  重 claim → 负载更高的死亡螺旋。`sweep_expired_claims` 的过期判定现在
  参考 worker 控制面存活：对越过 TTL 的 claimed/reporting execution，
  若其 worker 的 `last_seen_at` 仍在 online 窗口内（复用
  `ONLINE_THRESHOLD_SECONDS` 口径，与 code_dispatch 一致），本次不删
  租约、不重排队，打一条按 TTL 分桶降采样的 WARNING 让 execution 续命，
  worker 心跳面恢复后下一拍心跳即续期自愈。延期有硬兜底：心跳静默超过
  2×TTL（grace = TTL，严格小于）照常过期——worker 活着但某 attempt
  线程真死的场景不会永远挂着；控制面不新鲜（worker 真离线）行为完全
  不变。延期跳过的行既不计入 requeued 也不计入 done 的 runtime
  profile 口径。已知盲区（留二期）：worker 的 claim 循环只在领取预算
  为正时发 HTTP（`drain_budget` 按 `budget > 0` 循环），槽位占满 /
  `claim_enabled` 关闭 / 上传背压钳零时预算为 0、零控制面流量，
  `last_seen_at` 只剩心跳触活与结果提交触活——「全部槽位占满 + 心跳
  饿死」的纯饱和场景下控制面 30s 后照样 stale，延期分支不生效、回落
  旧行为（失败方向安全，不留僵尸容量）；二期方向是 worker 侧在预算
  为 0 时发轻量 keepalive。
- Worker 心跳与执行负载解耦（issue #566 二期+三期）：
  - **心跳 relay 挪到 supervisor 进程**：批量租约心跳不再跑在
    executor 进程内的 daemon 线程（机器饱和时抢不到 GIL，一期 deferral
    兜底的根因场景）。executor 按拍（2s 节流）把可续租约集合原子落盘
    为 `lease_snapshot.json`（含 worker token，mode 600，与 register
    token 同信任域）；supervisor 进程内的常驻 relay 线程
    （`worker/heartbeat_relay.py`）按快照发批量心跳（含 404/405 降级
    逐条、401 丢缓存 client 等 token 轮换），把 Host 的 lost/cancelled
    裁定写回 `lease_beat_result.json`，executor 主循环按 seq 幂等应用
    （lost 按 (execution_id, lease_id) 对匹配，重 claim 的新 attempt
    不被旧裁定误伤）。安全栏：relay 只在快照 pid 存活且快照新鲜
    （60s 停滞即停拍）时发拍——executor 脑死时租约按 Host TTL 正常
    过期重排，不会被冻结快照永远续命；executor 换代（快照 pid 变化）
    自动重探批量端点，降级不终身化。裸跑 executor（无快照 env）
    保持原进程内心跳循环。
  - **relay 存活看门狗**（PR #572 复审）：relay 每拍（含空裁定与瞬时
    失败）都重写结果文件并递增 seq 作为存活证明；executor 侧
    `worker/relay_sync.py` 的看门狗在持有租约而 seq 停跳超阈值
    （3×relay 拍间隔、下限 60s）时打一条 WARNING——区分「relay 活着
    无裁定」与「relay 死亡/supervisor 挂起」（后者租约静默过期会双跑）。
    纯观测信号，不改结果语义。
  - **一期盲区闭合**：relay 的每拍心跳都经 Host 鉴权路径触活
    `last_seen_at`，executor 整体饱和时控制面依然新鲜，一期 deferral
    在纯饱和场景也能生效（实测复核：budget=0 时 executor 主循环的
    状态同步 get_self 本就每拍触活，真正残留缺口只有 executor 进程
    级饥饿，由 relay 覆盖）。
  - **executor stdout 滚动持久化**：面板日志行（executor stdout +
    supervisor 生命周期）从仅有 500 行内存 deque 变为同时写滚动文件
    （10MB×5 轮转；state dir 在 `data/` 下时落
    `data/logs/executor-<state dir 名>.log`——文件名带 state dir 名，
    两个 state dir 同机共存不互踩；否则 `<state_dir>/logs/`），写失败
    降级为仅内存并报一次错，不影响采集线程。
  - **claim 负载回压 + 容量告警**：claim 预算按 1 分钟 load average
    衰减（≤核数不衰减，1×→3×核线性降至 0.25 下限——永不为零，共享/
    高负载机器上补位减速不停摆；预算应用向上取整，正预算至少保 1 槽；
    `os.getloadavg` 5s 缓存采样，不支持的平台直通不衰减），衰减/恢复
    跨档各打一条日志；回压只作用本地预算、不动对 Host 的声明容量。
    `max_concurrency` 超过 核数×4 时启动打 WARNING
    （`worker/load_shedding.py`）。

## [0.7.6] - 2026-09-09

### Added
- 容量旋钮收编进 admin 实例设置（issue #509/#554）：两组 Host 侧容量调参
  从「改 yaml/env + 重启」收编进 DB 实例设置文档（admin 全局设置 UI 可
  编辑，重启生效，与 `code_capacity` / `workflows.max_items_per_run`
  同形态）——
  - `agent_enqueue.workers`（默认 48，上限 256 防误配）/
    `max_pending`（默认 1024）：Host 入队线程池，#349 P1-1 承诺的
    「DB 实例设置」处置路径自此真实存在；runtime-profile「入队池饱和」
    分类指引同步指向 admin 实例设置。
  - `result_unpack.workers`（0 = 自动 min(4, 核数)，上限 64）：
    result 解包进程池尺寸（#552 下沉）；启动水合后 configure 惰性建池
    自然读到配置值。env `AGENT_LEGION_RESULT_UNPACK_WORKERS` 保留为
    覆盖通道（过渡期）。
  - 存量文档缺键回退代码默认，无数据迁移；`InstanceSettingsDocument`
    新增 `agent_enqueue` / `result_unpack` 嵌套块，PUT 全文档校验
    （workers 上限分别 256 / 64）。
- Worker 在线标记写入间隔收编进 admin 实例设置（issue #561，照
  #509/#554 模子）：#555 引入的
  `executor_runtime.agent_claim.worker_touch_interval_seconds`（默认
  30s，0 = 恢复逐次写）新增 `agent_claim` 嵌套块进实例设置文档，
  restart-effective（水合先于 broker 组装）；admin UI「队列与解包容量」
  组以用户视角命名「Worker 在线标记写入间隔（秒）」，说明不出现
  touch/last_seen_at 等实现术语。存量文档缺块回退默认 30s。

### Performance
- claim 锁面修复（issue #555，#546 回归的根治项）——三处叠加修法：
  - **扫描移出锁窗口**：batch claim 拆成「只读选候选」
    （`claim_batch_select.py`，read-only 连接、零锁）+「紧凑写入」
    （`claim_batch_tx.py` 只跑重校验 + promote）两段——不再拿着
    `agent-ws:*` / `agent-worker:*` advisory xact 锁与行锁跑
    `fetch_candidates` 扫描，持锁窗口从 O(批×扫描) 收回 O(纯写入)。
    选择段与写入段之间的竞态窗口由写入段逐候选重校验兜底
    （SKIP LOCKED 行探针 / job 状态重查 / 容量门 / 条件 promote），
    过期候选判 stale/raced 跳过，绝不半应用。锁前准入过滤单源化到
    `claim_admission.py`（单条与批两路共用，消除双轨漂移）。
  - **claim 不再重锁 running 的 jobs 行**：jobs promote 收窄为
    `where status='queued'`；多节点 job 的后继节点 claim 不再对同一
    热行做值不变的重写+重锁。rowcount=0 的两种语义（已 running
    vs 竞态出局）经 `FOR NO KEY UPDATE` 重读区分——与在飞的并发
    pause 串行化但不重写元组（review P1），后者仍判 ClaimRacedError
    回滚。
  - **touch_worker 节流**：claim promote 与 `mark_done` 的
    `agent_workers.last_seen_at` 写入改为距上次落盘超过
    `executor_runtime.agent_claim.worker_touch_interval_seconds`
    （默认 30s，0 = 恢复每次写）才写，谓词在 UPDATE 里——命中节流的
    touch 不匹配行、不取行锁；活性由 heartbeat 通道与 authenticate
    路径的 WorkerLiveness（#88）覆盖。heartbeat 路径不节流。

## [0.7.5] - 2026-09-09

### Performance
- result 提交的解包 CPU 段下沉进程池（issue #552，#521 根治项）：0.7.4 的
  batch claim 把执行面跑满后，完成波把 Host 单进程的 GIL 竞争推成新瓶颈
  ——result commit 的 tar/gzip 解包 + member 校验（`unpack_agent_result`）
  与 HTTP 面抢同一核，实测单核上限个位数 result/s、result POST 延迟恶化
  一个数量级、worker 上传队列积压并触发 90s 租约重发。修法：
  `unpack_agent_result` 是纯路径函数（无 DB 句柄/共享态），原样下沉
  `ProcessPoolExecutor`（默认 min(4, 核数)，`AGENT_LEGION_RESULT_UNPACK_WORKERS`
  可调）——调用线程停在 future.result() 的 GIL 释放等待上，N 核并行解包；
  坏包炸子进程不炸 HTTP 主进程；`finish`/`mark_done` 的短 DB 事务留在主
  进程。回归测试：池内真实解包 promote、坏包异常跨进程回传且池存活。

### Added
- 供给-消费全链路观测（issue #551）：
  - Worker 新事件 `execution.reported`（每上传任务一条）：
    queue_wait / prepare / transfer / report_wait / report 五段墙钟 +
    outcome（delivered/rejected/aborted）+ archive_bytes——上传管线从
    黑盒变成可分段定位；rejected（409）即租约重发的重复执行指纹。
  - Host claim 画像族新增 `queue_wait` 段（queued_at→promote 的供给延迟，
    per-promote 折叠，批内逐条计入；schema v81 落
    `claim_queue_wait_seconds_total/max` 两列），与既有 claim 阶段拆分
    （#448）、result 分段（#521）、enqueue 池深度（`enqueue_pending`）
    合成完整链路画像。
  - runbook §7 新增供给-消费链排障表（现象 → 指标列 → 判读）。

### 结构性拆分（预算纪律，无行为变化）
- `claim_evaluate.py` 的 promote 写入段 → `claim_promote.py`；
  `worker/upload/queue.py` 的 UploadTask → `worker/upload/task.py`（import
  路径不变，re-export）；`migration_chain.py` 的 SchemaMigration →
  `migration_entry.py`。claim_evaluate / migration_chain /
  worker/upload/queue 三个文件的 file_budget 豁免随拆分移除。

## [0.7.4] - 2026-09-08

### Performance
- batch claim（issue #546，hot-fix）：`POST /api/agent-executions/claim` 支持
  批领取——Worker 一次往返按分池申请（`agent_limit` / `code_limit` +
  总上限 `limit`）领至多 N 个执行，Host 在**一个写事务**内 promote
  （复用既有扫描阶梯、fairness 轮转、per-kind 尝试预算与跳过语义），
  补充速率上限从 ~350/分钟抬到数千/分钟。0.7.3 后实测触发条件已命中：
  瞬时 code 节点（0 秒执行）的自我吞噬循环把 claim 循环节拍吃掉一半
  以上，agent 容量爬不上去（供给与欲望都在，卡的是循环带宽）；批领取
  把节拍消耗从 N 次 RTT 降为 1 次。事务语义：`ClaimRacedError`（job
  中途离场）在批内经 SAVEPOINT 只回滚当前候选、保留前 k 个并终止本
  批，不再整事务回滚；其余候选级冲突（capacity_raced / shard 去重 /
  lock_raced）沿用既有跳过语义。兼容：缺省 `limit=1` 走原单条路径、
  响应逐字节不变；旧 Host 忽略批字段返回单条，新 Worker 形状嗅探自动
  回落逐条领取（混合舰队无协议版本 bump）。Worker 侧批大小由
  `claim_batch_limit`（默认 32，上限 256，热更）封顶，爬坡/背压/越池
  抑制经预算天然作用于批大小；pacing 输入改为批 RTT ÷ 批大小的等效
  单条 RTT（#472 自适应语义保留）。Host 侧硬顶 256/批
  （`agent_broker.claim_batch.MAX_BATCH_CLAIMS`，与批量心跳同纪律）。
  回归测试：limit=1 响应形状、批 promote/分池钳制/workspace 容量批内
  记账/竞态保留前 k 个/空批 204/混合舰队回落。
- enqueue 备货池默认并发 16 → 48（`executor_runtime.agent_enqueue.workers`）：
  batch claim 把消费侧抬到数千/分钟后供给侧（~1s/单的 staging+bundling
  闭包）成为瓶颈，实测备货池跟不上；每个闭包以 IO 为主，吞吐随 workers
  线性扩。

## [0.7.3] - 2026-09-08

### Fixed
- skill 无契约块时跳过 velites spawn（issue #538，hot-fix）：#521 落地的
  result 提交分段观测发现 validate 段秒级延迟的真因——契约引擎
  （`velites-sandbox validate`）每次 result 都 spawn 一轮，而当前 skills
  普遍不含 `yaml contract` 机器可读契约块（`references/output-contract.md`
  里只有 prose 与示例围栏），引擎空转（mode=existence 无裁决）纯耗服务
  时间，波峰排队下 validate 段上秒。
  修法：`server/app/workflows/output_contract_engine.py` 在 spawn 前进程
  内探测契约块（与 velites 同语义：strip 后恰为 ```yaml contract 的
  fence 行；无 `output-contract.md` → 无块），无块直接返回 None（None
  通道不变，legacy `validate_output.py` 照跑，Host 行为等价于今天的
  existence 回落），有块照常 spawn 由 velites 权威裁决（包括未闭合
  fence 等病态输入——探测层宁可误报让引擎 fail-closed，不做降级放
  水）。回归测试：无块/无文档不 spawn、有块照常 spawn、围栏变体
  （```yaml 不算、```yaml contract 算、大小写敏感）、未闭合 fence 与
  不可读文档仍 spawn。预期 validate 段分钟均值回到亚秒位。
- Worker claim 循环的分池预算泄漏（issue #534，hot-fix）：循环条件
  `budget["agent"] + budget["code"] > 0` 两池求和、扣减只扣实际领到的
  池——agent 池被爬坡/容量打满/上传背压钳到 0 而 code 池有预算时，
  agent 领取把 agent 预算扣成负值并借 code 预算继续循环（实测 -31），
  #471 爬坡门被完全绕过（冷启动 running 远超档位、claim 不受限）；
  Host 侧按 #501 声明的目标容量记账也不拦，本地预算是唯一的门。修
  为按池判定（`or`）+ 领到已尽池的活照单收下一个（Host 已记账，与
  「竞态超发照单收下」语义一致）后终止本轮。回归测试：泄漏场景
  （旧代码复现 agent_budget -1/-2/… 负值序列）+ code 池对照组；纯
  code / 纯 agent 场景行为不变。
- 越池 claim 的悬挂租约（#535 codex P1 复审，#534 修复的修复）：
  守卫原本放在 `pool.submit` 之前——Host 已记 claimed 的越池执行不被
  提交（不跑/不心跳/不报结果，只能等租约过期），爬坡期 Host 持续发
  活会逐轮累积悬挂租约。「照单收下」的语义必须含提交执行：break 移
  到 submit/active 记账之后。回归测试钉住「每个 claim 必被 submit」
  （旧形态复现：1 submitted vs 102 claims）；claim-loop 回归用例拆到
  姊妹文件 `test_agent_worker_claim_loop.py`（原文件 942 行，codex P2）。
- 越池抑制需跨 pass 生效且必须压 claim 声明容量（PR #539 codex 复审
  二轮 P1 + review P2-1）：仅 break 当前 pass 不够——Host 按「active
  < 声明容量」分池发活（#501 声明的是目标容量，不随爬坡档位走），
  本地预算只能 break 单个 pass，不压声明的话 Host 每个 pass 都会再发
  一个越池的活，running 一路爬到声明容量，ramp-up/背压同样被绕过。
  修复：**真越池**（预算已尽却领到该池的活，领取使预算转负）时把该
  池记入 `pool_deferred`，抑制期间该池 claim 声明压到
  `min(活跃数, 目标)`（Host 分池门即关闭）、预算视为 0；正常领满
  （预算 1 → 领取 → 0）不是越池，不抑制、不终止 pass——否则 ramp
  满档窗口声明容量会跌到档位值并随补位振荡，违反 #501「声明不随
  档位抖」（触发面必须用 `< 0` 而非 `<= 0` 判定）。解除面 = 该池
  「未被抑制时的预算」转正（avail > 0：执行完成/档位推进/背压消退
  ——比 base > 0 更严，背压钳 0 时 base 仍可 > 0，此时解除会立刻再
  越池），解除后声明回声目标容量。预算/声明推导收口到新模块
  `worker/claim_budget.py`（文件预算治理；`pass_budget` 对
  `pool_deferred` 的 discard 解除是显式 mutate 契约——executor 负责
  add、解除面与预算数学同址）。回归测试用 Host 分池记账的保真 fake
  （按声明容量发活、报果归还能名额）钉住：真越池抑制期间声明恒为
  (活跃数, 目标) 且不再被授权、执行完成后恢复；正常领满双池满档
  窗口声明恒为目标容量。

## [0.7.2] - 2026-09-08

### Added
- Host 单进程 result 提交路径分段耗时观测（issue #521，复用 #448 claim
  拆分模式）：`server/app/agent_broker/result_timing.py` 把一次 result
  commit 切成 unpack（tar/gzip 解包）/ artifacts_verify（Worker 直传
  产物 S3 校验/下载/晋升）/ validate（Host 侧输出校验）/
  artifacts_upload（本地产物镜像）/ lease_write（lease 终态写事务）/
  events（events.jsonl token 用量解析 + PI 压缩——两次全量扫描的现状
  数据，0.7.3 单遍合并的立项依据）/ mark_done（请求终态写事务）七段，
  每段一次 perf_counter + 一次 dict 写；每次 commit 输出一条阶段分解
  日志（DEBUG 常态，超过 `AGENT_LEGION_SLOW_RESULT_MS`（默认 15s）升
  WARNING）；分段折叠进 #359 运行画像（schema v80
  `result_stage_profile`，`ops_runtime_profile_samples` 落 14 列
  total+max，列只放迁移的 guarded ALTER 沿 v78 DDL 归属先例），采样/
  查询/契约/generated types 全链路打通；profile 折叠经 lazy import +
  best-effort 吞错，观测永不打断被观测的 commit。
- 遗留绝对路径一次性清理（issue #521 / #37）：启动时后台线程把
  `node_runs.log_path/run_dir/session_dir`、`jobs.storage_dir` 中
  `<data-dir-name>/<managed-category>/` 后缀可映射的存量绝对路径行重写
  为 data-dir 相对（复用 `resolve_data_path` 的后缀重定基规则，行访问
  走 JobQueries 门面 `jobs/queries/path_hygiene.py`——BOUNDARY-DATA-001，
  分块小事务、失败下次启动续跑、幂等由选取条件自带）；不可映射行保留
  并继续由启动报告暴露。修数据而非反复警告。

### Changed
- 遗留绝对路径警告按存储路径去重（issue #521）：热路径（result
  commit / claim / 仪表盘读）对同一存量遗留行每次读取都触发
  `logger.warning` + `warnings.warn`——进程内 set 按存储路径去重后同
  一路径每进程只警一次（`logger.warning` 的发射是真正的每次 CPU/IO
  开销，Python 默认 warning filter 只去重 `warnings.warn` 的显示），
  不同路径各自可见；测试的 per-tmp 路径语义不变。
- result 提交削峰信号量（issue #521）：完成波（DAG 同相位节点成波报
  告）下不受限的 GIL 绑定 commit 会打满单进程控制面饿死 claim/心跳，
  `agent_workers.max_concurrent_result_commits`（默认 16，0 = 关闭的
  kill-switch，instance settings 文档/契约/hydration 全链路支持、重
  启生效）经 `server/app/agent_broker/result_gate.py` 以
  `asyncio.Semaphore` 约束并发 commit 数；spool 不进门（慢速上传不占
  gate 槽），排队者作为协程等待不占线程池令牌；排队期间 lease 过期走
  既有 409 → sweeper 收尾语义。代价是波峰期 result 稍慢，换 claim/
  心跳存活。**注意**：PUT /api/admin/instance-settings 契约新增必填
  键（沿 max_items_per_run 的「PUT 去默认防静默重置」先例）——缓存的
  旧设置文档直接 PUT 会 422，需先 GET 再回写；调低
  AGENT_LEGION_DB_POOL_MAX_SIZE 时注意 gate 与连接池的配比（events
  段持读连接嵌套开写连接，建议 gate ≤ pool/2）。
- 运行画像内部拆分（issue #521 顺带，预算棘轮驱动）：stage 计量族
  （#448 claim + #521 result 的元组与折叠）拆到
  `runtime_profile/stage_gauges.py`、宽窗 rollup 拆到
  `runtime_profile/rollup.py`——counters/sampling 回到基线内，#448/
  #359 的两条 file_budget 豁免随之清账。

## [0.7.1] - 2026-09-07

### Added
- Agent 执行侧 JSON 字段级读写原语（issue #518）：velites 新增 opt-in 工具
  `json`——`op` 三态（`get` 按路径读字段、`set` 写任意 JSON 值、`delete` 删
  key/数组元素）+ JSON path 语法（`steps[2].content`、`["a key"].sub`）。
  动机：模型对自己产出的较大 JSON 改单个字段时，整文件重写费 token 且易错，
  曾退化为 bash heredoc 手写 python 读-改-写（高并发下的不稳定因素）；
  `get` 缺失路径报 null、`set`/`delete` 对缺失中间 key 报错不自动建，文件
  写回走 tmp+rename 原子替换（与 write 同协议）、路径沙箱同 write，skill 里
  各自携带的 `json_patch.py` 应退役。工具目录机制（#476）验证：velites 一处
  新增工具，catalog 契约测试与 Studio 选项面（opt-in 档）自动跟上。
- Studio 提示词面板重命名与布局（issue #513）：「平台信封」更名「平台提示词」
  （说明：根据 workflow 自动生成，不可修改）并上移置顶；「节点指令」更名
  「节点附加提示词」（说明：内容由用户自由编辑，会追加到平台提示词最后组成
  完整运行提示词，默认留空）。仅面板命名/文案/顺序调整，prompt 拼装行为
  不变。
- Studio 工具选项硬编码根治：velites 工具目录自描述 + per-runtime 动态
  发现（issue #476，收编 #464-2，合并实现 #449 的 dispatch 校验）。新增
  `velites tools list --json` 子命令输出工具目录（name/tier/description/
  parameters，forced 档带 activation）；Host 侧 runtime catalog adapter
  声明 per-runtime 工具目录（velites 静态镜像与二进制输出全等，跨二进制
  契约测试钉住；pi 外部 runtime 按实测静态登记三件套），`GET
  /api/agent-runtimes` 按 runtime 嵌套暴露。tier 三档模型：`default`
  （read/write/bash，预选中可取消）/ `opt-in`（uuid，显式开启）/
  `forced`（validate——非用户选择，激活条件 `--require-output` 成立时
  harness 自动广告，UI 渲染锁定行不提供 checkbox）。velites 侧联动：
  `--require-output` 非空且首个 skill 目录声明可解析 contract block 时
  自动把 validate 加入广告工具集（parse error / 无 contract 不激活——
  模型修不了只读目录里的语法错误）；`--tools validate` 向后兼容为 no-op。
  dispatch 期工具名校验（#449）：节点级声明优先、Agent 定义兜底，两来源
  都在 catalog 目录上 fail-fast（未知工具拒发、forced 档静默剔除），
  Studio 动态选项面与校验同一数据源。AgentEditor 工具选项按所选
  runtime 动态渲染（默认值不再前端硬编码三件套，来自目录 default 档），
  runtime 切换后失效工具显式标记并提示剔除（把 dispatch fail-fast 前移
  到编辑体验）；agent 节点详情补节点级 `tools:` 声明编辑入口（#443 的
  Studio 补课，空 = 跟随 Agent 定义）。`AgentDefinition.tools` 默认值与
  API/MCP 三处硬编码统一改从 catalog 目录派生（值不变，单一来源）。

## [0.7.0] - 2026-09-06

### Added
- Agent Worker 执行链路结构化事件日志（issue #490）：Host 与 Worker 两侧
  统一 JSON lines 事件（`event` / `ts` + 语义载荷），按 `execution_id` /
  `worker_id` 对齐时间线。Host 侧 `worker.registered` / `worker.offline`
  （last_seen 越过 30s 阈值的转移检测，每转移一条）/ `claim.granted` /
  `claim.empty` / `claim.rejected`（reason 码直读归因：capacity_full /
  runtime_mismatch / model_mismatch / workspace_not_allowed 等，判定点
  命名与 claim 逻辑一一对应）/ `execution.finished` /
  `execution.lease_expired` 落 `agent_legion.worker_events` logger——
  uvicorn log-config 显式挂载 `agent_legion` logger，转折事件 INFO 默认
  可见、正常节奏 DEBUG 排障窗口按需拉起；Worker 侧 `claim.attempt` /
  `claim.backoff` / `execution.completed` / `http.error`（中间层 5xx 的
  唯一观测位：状态码 + 目标 URL + 截断 body，URL 剥查询串防 token 泄漏）
  沿 supervisor console 流。uvicorn 访问日志 formatter 补时间戳，跨
  worker 时间线从行号近似升级为墙钟对齐。事件码表与 reason 对照见
  remote-execution-runbook §7.1。
- 冷启动容量爬坡节流 ramp-up（issue #471）：Worker 的 `ramp_up` 配置块
  （`initial` / `step` / `interval_seconds`，缺省 1 / 1 / 60s；null 或缺
  块 = 禁用回现状）控制积压释放节奏——发布重启 / claim 重启 / 批量
  run 提交后恢复调度时，数百个 agent 不再同时发起首次 LLM 请求打满
  provider。生效容量从 initial 起步、按 interval 阶梯放量到目标后窗口
  永久关闭；只升不降（窗口内热更更小 initial 不回撤在途档位）；claim
  暂停期间虚拟时钟不前进（恢复时折回暂停跨度，停领一小时的 Worker
  恢复后不直接跳到高档）；控制台高级参数区可编辑（未勾选提交 null 即
  时禁用），容量卡显示「容量爬坡中 e/t」进度。与 claim pacing
  （#472）正交：pacing 管两次 claim 之间的等待，ramp-up 管本 pass 最多
  领多少。
- Worker 一键安装脚本 `scripts/install-worker.sh` + 独立部署编排
  `deploy/compose.worker.standalone.yaml`：无仓库克隆的机器经
  `curl | sh` 组装独立 Worker 部署（拉取发布 compose、sha256 校验下载
  velites 二进制、生成引导 worker.yaml / models.json），幂等语义分层
  （自有资产刷新到目标版本，用户资产绝不覆盖）；standalone compose
  新增 `AGENT_WORKER_UI_BIND` / `AGENT_WORKER_UI_PORT` 端口插值。
- Worker 镜像发布管道（worker-image-release workflow）：`worker-v*` tag push
  时以原生 runner（amd64 / arm64，不用 QEMU）构建 worker 镜像，按 digest
  合成 manifest list 后推送 GHCR（`ghcr.io/luciuscao/agent-legion-worker`，
  打版本 / sha-<短哈希> / latest 三个 tag）；新增拉取式 compose override
  示例 `deploy/compose.worker.pull.example.yaml`（`!reset` 清 build 段后
  `make stack-worker-up` 直接用 registry 镜像），部署文档 §5 增补「拉取式
  部署」小节。
- 原生形态绑定地址覆盖（#480/#482）：`NATIVE_BACKEND_BIND` /
  `NATIVE_WORKER_BIND`（默认 `127.0.0.1`，不设置行为不变）把 `make
  prod-up` 原生形态的 uvicorn / worker.service `--host` 从硬编码 loopback
  放开到局域网 / overlay 地址；健康检查探测地址按 bind 派生（通配归一
  loopback、IPv6 括号化），幂等判定与停机定位按「bind 地址 + 端口」精确
  匹配（同端口不同地址可并存不误判、不杀错进程）。对象存储
  `AGENT_LEGION_S3_BIND` 两形态通用，绑具体 IP 时原生后端的
  `AGENT_LEGION_S3_ENDPOINT` 需同步指向该地址（见部署文档 §2）。

### Changed
- 运行提交路径分块化 + 响应瘦身（#467 子项 A，Refs #420）：`POST /runs`
  的逐 item DB 往返改为分块集合探测（materials/bundles/ref 连接键各一个
  IN 查询/500 条），workspace 全量 dedup 键扫描改按本次 items 的键做索引
  点查，`create_jobs_bulk` 从单事务改为 ≤1000 行分块事务（每块事务内
  FOR KEY SHARE 先锁本块引用的 material/bundle 行再插入，块提交即释放
  ——任何删除时序下都不会插入引用已删材料的 job；身份冲突在首个 chunk
  提交前全量检测），`RunCreateResponse` 不再物化 job 行（run +
  created_count；前端 toast 只读 created_count，job 列表/详情走读取
  路径）。**行为变化**：分块提交下中途失败不再是全有或全无——已提交
  chunk 的 job 保留、run 行落 `failed` 态并携带已创建进度
  （`created_so_far`/`run_id` 进 400 detail），重提交同一批 items 经
  dedup 自动跳过已创建部分（run 行治愈为 created、计数累计）；剔除坏
  item 后重提会因 digest 变化产生新 run 行（dedup 保证 job 不重复）。
  验收实测：单请求 5000 items 提交 6.9s，`/api/health` p95 29ms。
- Agent Worker 心跳批量化（issue #352，协议 v5）：per-Worker 批量续期
  端点 `POST /api/agent-executions/heartbeats`——Worker 侧每执行一条
  心跳线程合并为本机单个批量循环，一次请求覆盖全部在跑执行（含排队
  上传任务的租约），Host 侧单写事务完成整批续期；心跳的**事务数、
  commit fsync 与 HTTP 往返**从 O(在跑执行数) 降为 O(机器数)——DB 行级
  写次数仍 O(槽)（逐项 lease 判定要求逐行语义），但每机每拍从 N 事务
  收敛为 1 事务，不再随槽数线性放大事务开销。逐项语义与单条
  心跳完全一致（未知/过期/跨 worker 项逐项进 lost，不抛 5xx、不阻断
  同批其余项）；单批上限 256 项、超限自动分片（高槽位是合法配置，不
  再有超限拒打悬崖）；zombie（agent 进程已退出且未被上传收养）停跳，
  Host 孤儿 sweeper 可回收。混合舰队兼容：单条端点保留且行为完全不变
  （旧 Worker 对升级后 Host 语义零变化）；新 Worker 对批量路由缺席
  （404/405）的 Host 自动降级逐条心跳（降级路径逐条带 5s 短超时；严格
  的 pre-v5 Host 在注册握手处即拒绝新 Worker，该组合走不到降级路径）。
  升级顺序 Host first, Worker second；协议 v4↔v5 双向兼容经实测验收。
- Worker claim 成功路径自适应 pacing（issue #472）：0.2s 固定等待改为
  「上一次单次成功 claim 往返 × 0.5，钳入 [10ms, 100ms] 带」——旧固定
  间隔把有效 claim 速率钉在 1/(0.2s + 往返)（~5/s 量级）的数学上限，
  带内映射后随往返实测自适应（往返变快立即回落、变慢按比例抬升；
  往返 70ms 场景 ~3.7/s → ~12.5/s，且随往返继续改善自动跟进；验收
  实测 pacing 落位 10ms 下沿）。三路径分工不变：空队列维持
  `poll_interval`、错误路径维持 #437 的指数退避序列；批量 pass 喂给
  pacing 的是最后一次成功 claim 的单次往返（非批次总墙钟，爬坡期不被
  批次规模稀释）。10ms 下沿为 claim 写事务间的锁争用保留呼吸护栏。
  pacing 变化经 `worker claim pacing <N>ms` 日志判变（同显示精度内不
  重记，稳态零日志量）。
- 远程分片并发解除串行化（issue #401，schema v79）：`agent_execution_
  requests` 的单活跃请求索引从 `(job_id, node_key)` 宽化为纳入分片身份
  的表达式索引（`coalesce(manifest_json->>'shard_index', -1)`；非 shard
  行身份恒为 -1，单活跃语义逐字保留）——多分片大节点不再每个同时只有
  1 个远程分片在飞，并发上限回到 fleet 声明容量（与本地 lease 路径
  对齐；验收实测 2 shard 并发在飞）。配套：code_stock 门新增单 pass
  fan-out 预算（拆串行化后单 pass 不再可灌洪峰，跨 pass 自然续消费）；
  shard 产物契约收窄为 per-index 的 `shard_output-<index>.json`（普通
  `node.outputs` 从 shard 的 expected_outputs 排除，兄弟分片产物不再
  互踩；本地路径同步收窄，两侧行为一致）。
- 预览面板安全收口（issue #347，PR #475/#477）：定制预览对话框期间的
  agent 草稿不再自动执行——左栏默认渲染已发布版本，对话框 footer 显式
  「预览此草稿」动作点击后才挂载草稿 iframe；授权随草稿 null 过渡
  （发布/归档）与路由身份（jobId/workspaceId）变化复位，同会话的新
  草稿 / 新 job 上下文不继承旧授权，重新打开对话框回到默认态。published
  路径自动渲染行为不变。questionPanel 的 boot 竞态（慢的旧 init 结果
  覆盖新内容且不再自愈）加 generation 守卫（#475）。
- velites 工具执行四相位打点（issue #469）：`tool_execution_end.timing`
  新增 `ToolTiming`（velites 扩展，全 Option 字段、缺省跳过）：
  `totalMs`（分发开始 → 结果就绪，分解基座 total ≈ spawnMs + firstByteMs
  + restMs + reapMs）、`spawnMs`（进程创建，含沙箱包装 exec）、
  `firstByteMs`（spawn 返回 → 管道首字节，完整覆盖子进程前置链路：
  bash 解析、内部 heredoc write、解释器启动）、`restMs`（首字节 → 子
  进程退出）、`reapMs`（仅超时/取消击杀路径）、`requestedTimeoutMs`
  （实际执行的 timeout 上限，区分「模型要了长上限」与「正常上限内挂
  死」）。判读表：write 侧阻塞（子进程卡在产出首字节前——#469 的
  spindump 主形态）表现为 `firstByteMs` ABSENT（整窗落入 restMs 后超时
  击杀）、read 侧阻塞（harness 读挂起）表现为 firstByteMs ELEVATED。
  进程内工具（read/write/uuid/validate）仅报 totalMs；测量前失败（参数
  校验 / guard 拒绝）不携带 timing。bash 的 stdout/stderr 读取从
  read_to_end 改增量读以观测首块边界，字节收集 / 顺序 / 截断 / 超时
  语义全部不变。Host 消费面向后兼容（不识别新字段时忽略）。

### Fixed
- secret 三通道 fail-fast（issue #432）：draft YAML `node.config` 通道的
  secret 值（明文字符串或 `{"secret_set": true}` 回显形态）在 intake /
  dispatch 重解析 / job workflow upgrade 三链拒绝（错误只报字段名与
  vault 通道指引，绝不回显提交值），发布门禁同步收紧（publish 即失败，
  而非发布后该 workspace 每个新 job 的 intake 一起挂）；`config_schema`
  与 Agent 定义声明 `secret: true` 属性带明文 `default` 即拒（声明侧
  唯一校验点，Agent 定义 draft 保存即失败）。修复前经旧缺口发布的存量
  active revision，升级后其新 job intake 会 422 硬失败（fail-closed 是
  刻意立场：这类明文本身已是 VAULT-SECRET-001 违规数据）——恢复路径：
  draft 删除 secret 字段 → 发布干净 revision → 经 settings nodeConfig
  PATCH（唯一 vault 通道）重新写入。
- gate 排队 TOCTOU（issue #488）：`scripts/gate-queue.sh` 的 `_slot_mtime`
  probe-then-query 两次 `stat` 之间 slot 文件并发消失（正常排队行为的
  yielding 设计）被 `set -e` 放大为整个 pre-push 失败——多 gate 排队时
  排队几十分钟白排。修为单次捕获调用，文件消失读作「无 mtime」回退
  age 0；同族的真实 kill 路径（`_reclaim_stale_gate_slots` 的 head 读取）
  与其余「读取后假设存在」的调用点逐一防御（`gate-jobs.sh` 对称加固）。
  排队语义零变化（slot 计数、TTL 回收、holder 打印、等待节奏不变）。

版本线对齐：
- pyproject 0.6.0 → 0.7.0 + uv.lock 同步；frontend 0.4.0-alpha 落版一致
  性经 check_versions 解耦纪律验证通过。
- velites 0.5.0 → 0.5.1 落版（0.7.0 后置提交）：velites-v0.5.0 tag 之后
  velites/ 子树有 #469 工具相位打点的四个源码 commit（判读表内核取证
  修正、measured 失败样本保 totalMs、边界后首字节不进 firstByteMs、
  requestedTimeoutMs），独立版本线随源码前进——三平台二进制经
  velites-v0.5.1 tag 发布。
- velites 0.5.1 → 0.5.2 落版（0.7.1 补丁线后置提交）：velites-v0.5.1 tag
  之后 velites/ 子树有 0.7.1 的两批源码改动——#476 工具目录自描述
  （`velites tools list --json`、三档 tier、validate 的 --require-output
  forced 联动）与 #518 json 工具（get/set/delete JSON path 读改写），
  json 为新工具面（旧 0.5.1 二进制遇 `--tools json` 启动即报错，Host
  catalog 已 advertise——版本号区分二进制新旧避免部署漂移误判）。
  独立版本线随源码前进——三平台二进制经 velites-v0.5.2 tag 发布。

## [0.6.0] - 2026-09-05

### Added
- agent 发起 workflow 发布的确认回路（issue #416，schema v76）：Studio
  chat 的 agent 经新 MCP 工具 `request_workflow_publish` 挂起发布请求
  （pending 状态机，永不自行确认），用户在 Studio 发布确认对话框里
  审阅 diff 后 confirmed / rejected；`get_publish_request_status` 供
  agent 轮询结果，同 workspace 后到请求自动 supersede。
- velites 内置工具扩充（0.6.0 后落版 velites 0.5.0）：`uuid` 工具
  （生成与校验，校验拒绝未定义版本位，#442/#465）；`validate` 工具 +
  require-output 契约关卡（#443）——skill 可声明节点必填输出，执行末
  违约即失败，host 镜像同步烤入 velites-sandbox 包装器。
- Studio code 节点配置面板重设计（#418）：schema 结构化编辑 + config
  双通道（revision 快照 vs workspace live 覆盖）明示；workflow compare
  补 config/config_schema 比对（#418/#422），纯配置变更不再无法发布。
- Skill 选择链路合一（#410）：节点 skill 选择收敛为「目录 + 版本」
  两控件，回显实际执行版本（`node_runs.skill` 记录 dispatch 时的
  skill key，schema v75）。
- 创建 Agent 表单隐藏 Agent ID（#407）：服务端按 capability 生成
  agent_id，显式传值保持旧客户端契约不变（MCP copy/save 路径零改动）；
  Agent 发布触发 workspace override prune（#430），与 revision 发布
  链路对齐。
- 外部内容 ref 的连接 Key 改为选择控件（#419）：唯一 key 默认选中。
- claim 吞吐第一阶段观测与批量化（#448/#461，schema v78）：claim 事务
  切段计时（worker_setup/scan/evaluate/writes 落 ops profile 采样），
  `create_jobs_bulk` 改 set-based INSERT（每批 1000 行）——v77 statement
  触发器从每行一次变为每批一次。

### Changed
- Workflow Studio 按节点类型收口「配置 Schema」归属（#406）：
  `type: agent` 节点不再渲染节点 YAML 的 `config_schema` 区块，
  Agent schema 统一归「Agent 配置」内的 Agent Definition 编辑入口；
  `type: code` 节点的 schema 编辑与 `runtime_mutable` 行为保持不变。
- Studio agent 节点的 runtime 默认值改为 velites（#408）；检查器 Agent
  区块内联展开，去掉开合按钮与重复汇总卡片（#409）。
- Worker 代理出口产品化（#444）：worker 入口剥离继承的代理 env（LLM
  流量不再意外经本机代理中转），需要代理时在 worker.yaml 声明显式
  `proxy` 字段（替代 `WORKER_KEEP_PROXY_ENV` env 逃生门）。

### Fixed
- workflow compare 快照往返与比对完整性（#431/#454/#458）：compare 补
  node_type / shard / reduce / after 序 / edges 序比对；reduce 快照
  `from_node` 不翻译、definition_to_yaml 不回显 shard/reduce 两个往返
  缺陷修复——含 shard/reduce 基线的工作区不再一打开就有幽灵变更，
  照此发布会静默删分片的路径已堵死；纯边重排的 DAG 高亮修正。
- 高并发档位 job 状态计数触发器热点行死锁（issue #437）：高并发、
  单 run 大规模 items 下 claim 间歇 500（psycopg DeadlockDetected，落点
  claim 事务内 jobs promote UPDATE），并发呈锯齿式波动。根因
  是 v73/v36 的 run/workspace 级行级计数触发器把同一 run 全部状态迁移
  汇聚到寥寥几行 (run_id, status) 计数行——先扣旧 status 行再加新
  status 行的两步锁足迹，与 claim 的 queued→running、收尾的
  running→completed 以不同顺序触碰交叠成锁环。三层修复：① 根治
  （schema v77）：两组计数触发器改为 statement-level + transition
  tables——单语句内按 (key, status) 聚合净增量、按固定字典序一次性
  apply，所有并发写方锁序全局一致，锁环不再成立。收益在固定锁序与
  死锁消除，不在触发次数：psycopg executemany 服务端仍是 N 条独立
  INSERT（每条触发一次 statement 触发器、transition table 1 行），
  与旧行级触发器逐行加计数同量级；多行单语句（INSERT...SELECT）才
  会一次聚合，当前代码库无该形状；
  ② 缓解：claim 端点对 SQLSTATE 40P01 立即整体重试一次（干净连接
  重进事务，再失败放行 500）；③ 缓解：Worker claim 退避改「首次 1s
  固定 → 之后指数翻倍 ±20% jitter，上限 60s 不变」——瞬时抖动不再
  烧掉完整 poll 周期，fleet 恢复不再同步对齐（锯齿根因之一）。
- Studio 对话 run token 连锁失效与静默死亡（issue #411）：单轮 prompt 可
  跑满 1 小时，而 run token 续期只在轮首（30 分钟阈值）——长对话的 token
  会在 turn 进行中过期，agent 的全部 MCP 工具调用 401（"Studio agent
  scoped token required" → 客户端 "Not connected"），且界面无任何提示。
  修复三处：① 每次 `tool_call` 事件触发保活（`studio_chat/token_keepalive.py`）——
  token 活着则以「整轮时长 + 5 分钟」的专用阈值顺带续期（检查过存活的
  token 必然活过当前轮，防泄漏语义不变：已吊销/已过期不复活），token 已死
  （吊销/过期/用户被禁用）则向会话时间线追加一条 `run_token_invalidated`
  状态消息，前端以警示样式提示「关闭当前会话后点『继续对话』恢复」；
  ② `list_studio_chat_messages` 的 500 条上限从「取最早 500 条」改为
  「取最新 500 条」（`order by seq desc` + 反转，返回值仍为升序）——
  超长会话重进界面不再只看到远古记录而丢失进行中的对话（即 issue 报告的
  「聊天记录消失」）；③ 保活与提示的 DB 操作全部带异常保护，失败不阻断
  tool_call 消息落库且下次 tool_call 自动重试；续期 UPDATE 的 rowcount
  闭合「查活→续期」间隙内 token 被吊销/过期的竞态（未命中即重验存活，
  最后一次工具调用也不会漏报失效）。已知取舍：掉线超过 500 条的增量补齐
  会在新旧窗口间留缝隙（API 无 before_seq），重新进入会话即全量替换自愈。
- 事务异常后连接复位（issue #438）：`write_transaction` 在非 autocommit
  连接上的双重 BEGIN 根治（每次 claim/heartbeat/enqueue 检出一轮就向
  Postgres 发一次冗余 BEGIN，服务端 WARN 噪声）+ 连接池 reset 防御，
  事务失败后连接不再带坏状态回池。
- 单副本探针 idle in transaction（issue #433）：probe() 取锁 SELECT 在
  池化连接上隐式开事务、fetchone 后未提交即长持——backend_xmin 钉死在
  进程启动时刻，autovacuum 无法回收死元组；取锁后补 commit 消除。
- Agent 创建占用检查跨版本缺口（发布 P1，#407 后续）：占用检查补已发布
  版本行扫描——已发布 capability A v1 后保存 capability B 草稿时，缺省
  创建 A 不再静默覆盖用户正在编辑的草稿（409 + 指引文案）。
- DAG 视图缺边回退与无边图布局退化（#417）：dagre 对无边图按字母序竖排
  造成「节点丢失」观感；workspace_dag 与 job 详情的 after 序同源化。

## [0.5.0] - 2026-09-03

### Added
- 人工审批节点（issue #266，schema v63，EXEC-APPROVAL-001）：`type: approval`
  节点是 DAG 内的人工决策关卡——调度器 park 至 `awaiting_approval`
  （不派发、无租约），决定仅经审批 API 由人类会话作出（studio agent
  scoped token 一律拒绝），`approval_decisions` 只增不改留痕：approved
  写 verdict 产物后放行（条件边可按 `$.verdict` 分支）、rework 意见必填
  并经 rerun 机制重置上游、rejected 节点失败并结束任务。
- Studio 草稿显性保存与防丢（#331）：保存按钮 + 五态状态文本、
  DraftSaveController 状态机（flushNow / 失败退避重试）、页面离开防丢
  （visibilitychange/pagehide flush + beforeunload 拦截）、画布渲染草稿
  （draft/revision 三态标识，YAML 非法回退 published + 警示）。
- 运行画像 L1+L2（issue #359，schema v72）：六段管线指标
  （`ops_runtime_profile_samples`，ops-metrics 采样循环每分钟落一行）+
  瓶颈归因分类器 + API——批量运行期间实时回答「当前瓶颈在哪个阶段」。
- run 计数快照与首屏 COUNT 缓存（issue #358，schema v73）：触发器维护的
  `run_job_status_counts` 把 run 详情读取从全量 group-by 变成 PK 点查，
  items 上限硬约束同批落地。
- 执行面 retention 管道（issue #354）：agent manifest trim + 终态行窗口
  删除 + sweeper 单副本收拢——核心执行面表此前只写不删，长期运行后
  累积的终态行不再拖慢后续批次（大表场景收益显著）。
- Studio 节点类型选择器与画布创建入口（#392）：前端对齐后端
  `start|code|agent|approval` 类型抽象——类型切换前置校验 + 确认弹窗，
  按类型注册 inspector section 集，approval 画布可见与节点创建入口。
- Studio 会话面板 agent 配置区（issue #368）：权限模式 / 模型 / 思考档位
  的可见可切控制面（ACP 会话 modes/configOptions 广告驱动，未知档位
  保留回退，通用思考档位映射 off<minimal<low<medium<high<xhigh<max<ultra）。
- draft-only Agent 补齐 Studio 发布入口（#387）：MCP 建的草稿在节点
  检查器可解析（published 优先、draft 回落），聊天草稿卡片导航空转修复。
- 预览面板安全与正确性修复（PR #345 codex 评审 P1/P2）：宿主在 srcDoc 的
  `<head>` 注入 CSP（`default-src 'none'` + 平台资源白名单 + `connect-src` 限
  平台 origin），堵死沙箱 bundle 的出站网络通道（`sandbox="allow-scripts"` 不
  阻 `fetch`/`sendBeacon`/`<img>` 外传——恶意草稿可先经桥读任务数据再外发）；
  `PreviewPanelSection` 的 remount key 加入 bundle 内容指纹，草稿轮询更新时
  整树重挂 iframe，旧文档在途桥请求的响应不再可能错误应答新文档的同编号请求；
  authoring context 的 `recent_jobs` 产物清单统一走本地目录 ∪ 对象存储
  manifest（此前仅 selected job 合并，worker 执行任务的 recent 清单会报空）。
  preview_guide.md 运行时契约同步（出站网络由宿主强制而非编写约定）。
- 发版解耦纪律 + 版本清单一致性检查（`scripts/check_versions.py`，挂 backend
  静态轮）：velites（`velites/Cargo.toml`）与 frontend（`frontend/package.json`）
  持有独立版本线，禁止随仓库版本（`pyproject.toml`）锁步 bump——无谓的版本前进
  会改变 velites 子树 tree hash（`ensure-velites.sh` 的二进制新鲜度指纹）与
  Docker 缓存键，触发全量 `cargo build` / 镜像层重建。检查两条规则：清单 ↔
  lock 版本一致；独立组件的版本前进必须伴随锚点以来的源码改动（仓库发版顺手
  bump 无源码改动的组件会被拒绝）。规则详见 `scripts/check_versions.py`
  模块 docstring 与 CONTRIBUTING「House rules」。
- Workflow nodes declare an explicit execution type `type: code | agent`
  (issue #284 phase 2, schema v66): the publish gate branches on it
  (agent nodes require exactly one published Agent for the capability,
  code nodes require published node code), revision publication
  materializes Agent routes only for `type: agent` nodes, and the startup
  route reconcile is retired — routes now change only at revision
  publication. Legacy `type: node` and an omitted type normalize to
  `code`; the v66 migration backfills stored active revisions and Studio
  drafts from the route projection.
- 架构盘点：workflow_key 退役 Phase 1 分类清单（`docs/architecture/workflow-key-retirement-inventory.md`，issue #211）——四类穷尽引用 + Phase 2-4 执行依据。
- Host 侧 agent runtime catalog（`server/app/agent_runtime`，issue #75）：
  runtime 全集单一事实来源（`AGENT_RUNTIMES`）+ 每 runtime 一个 adapter
  （argv 构建 + `ExecutionContract`）；「新增 agent runtime 接入指南」见
  `docs/architecture/velites-harness.md`。

### Changed
- host 纯控制面模式：workflow 执行与宿主进程解耦（#389，收编 #385/#386）。
  `code_capacity` 合法化 0 值（契约 `gt=0→ge=0`，UI「代码池」组改述为
  「本地执行」——本地兜底执行并发上限，0 = 纯远程模式）：宿主容量为 0 时
  不再组装本地执行栈（CodeExecutor/ExecutionRuntime/线程池/velites 沙箱
  依赖全部消失），code 节点 100% 由远程 code Worker 执行；shard 分片执行
  远程化——分片身份（`shard_index`/`shard_input`）写入持久化 manifest，
  broker claim 事务经 `try_start_shard` 绑定 `node_shards` 行（行级去重），
  分片输出以 `shard_output-<index>.json` 作为常规 expected_output 随归档
  回传（不走尺寸受限的 metadata 通道）；调度线程 pass 级早退修复（纯远程
  部署不再被饿死，且保留审批门等免 dispatch 工作的处理机会）；
  `/api/health` 在纯远程模式下实时报告在线 code Worker 数（启动为 0 打
  WARNING），防静默停摆。
- `workflows.enabled` 退役（#385，由 #389 第 3 步收编）：该开关已从灰度
  开关漂移为事实上的产品总开关，单机部署无合理关闭场景。404 门禁
  `require_workflows_enabled` 整体移除（38 个路由文件、约 150 处调用，
  API 面永远可用）；`worker_startup.is_enabled` 分支删除（worker 总是
  启动，部署形态改由 `code_capacity` 表达）；实例设置契约删除该键，
  存量 DB 文档读取时键级剥离（`workflows.max_items_per_run` 活跃保留），
  无数据迁移。升级窗口内旧前端整文档 PUT 携带该键会 422（破坏性契约
  变更，刷新前端即恢复）。
- worker 镜像与 agent runtime 解耦（#381/#383，PR #384）：velites/pi 移出
  worker 镜像——镜像收敛为纯执行服务（Python worker + bwrap + 内置的
  `velites-sandbox` code 沙箱包装器），velites agent runtime 以平台匹配的
  外挂二进制提供（compose bind mount 到 `/app/data/bin/velites`，long
  syntax 缺源拒启）。新增 `AGENT_WORKER_EXPECT_RUNTIMES` 期望 runtime 守卫
  （探测不到/被停用/模型发现失败均 fail-fast，退出码 2）；注册 payload 携带
  生效 runtime 的 `--version`（版本握手可观测，外挂后的漂移排障依据）。
  pi 在 docker 镜像内不可用（npm 入口依赖 node），部署走裸机。新增
  `velites-v*` tag 触发的三平台 release workflow（linux amd64/arm64、
  macos arm64）；host 容器的 code 本地兜底禁用（避免为兜底路径给后端
  容器加 seccomp/cap 特权）。
- Worker agent runtime 声明改为自动探测 + 反选停用（issue #254）：Worker
  控制台的 runtime opt-in checkbox 退役——runtime 选择在 Studio 节点
  （Agent 定义）上，Worker 侧按本机二进制探测（自带副本 `data/bin`
  优先、PATH 兜底）默认全部启用，`disabled_runtimes` 反选停用；生效
  runtimes = 探测 − 停用，每次读取现算（勾了没装 → 预检拒启动；装了
  没勾 → 任务永远 runtime_mismatch 的配错面消失）。
- Skill 版本绑定下放到 workflow 节点（issue #76）：节点的 `skill` +
  `ref` 是版本绑定的权威位置，`skill_lock` 多值化（v2 `{repo, refs}`），
  `AgentDefinition.skill` 降为节点未声明时的兜底。
- 节点 `skill.ref` 语义显式化（issue #322）：`latest`（空 ref 已归一为它）= 跟随 skill 仓库 HEAD，每次 dispatch 现场解析、永不入锁；具体 tag = 首次 dispatch 把解析的 commit 冻结进 `skill_lock`（v2 多值 `{repo, refs}`），唯一 relock 通道为 CLI `make skills-lock`（遍历锁内已有条目重解析 pinned refs）。**行为变化**：存量 published revision 中 ref 为空的节点原先冻结在 skill source 默认 ref 的 commit 上，升级后改为跟随仓库 HEAD；需要复现的节点应在 Studio 草稿中显式 pin tag 并重新发布。
- Agent execution 契约 runtime 化（issue #75）：Host 侧 runtime catalog
  （`server/app/agent_runtime`）的每个 adapter 声明自己支持的 manifest
  execution 键（provider/model/thinking）与必填性；dispatch 与 Worker claim
  重解析统一按契约校验——配置了 runtime 不支持的键（非空值）或必填键在
  解析链上不再有来源时 fail-fast。**行为变化**：在飞 job 跨 revision 升级
  后若节点引入了 runtime 不支持的 execution 键（或必填键不再可解析），
  claim 从静默下发变为可行动报错（claim 扫描跳过该候选，unclaimable
  sweeper 将请求判失败并给出指向节点 execution 覆盖的错误信息）。

### Deprecated
- workflow_key 兼容窗口期公告（issue #211）：全部 deprecated 契约面的迁移文案统一标注移除时间 **2026-10-31**——27 个请求/响应字段、10 条 URL 别名、claim 协议字段将在终态批移除。显式发送恒等值（=workspace id）继续放行至该日期；不匹配值已由守卫拒绝（400）。所有部署实例须在窗口期内升级至 ≥ schema v68（存量 workflow_key 已对齐）。

### Removed
- 全局 skill_sources 注册表整体退役（issue #322 决策项 1）：skill 收敛为 `~/.agents/skills/<group>/<name>` 下的本地 in-place git 仓库（唯一模式），删除远程 clone 通道、repo 漂移闸门与缓存缺失 re-clone 自愈（缓存缺失改为报错并指引在 skill root 下创建）；admin `/api/admin/skill-sources*` 端点与「Skill 源管理」设置面板一并删除，`skill_lock` 的 `repo` 字段退化为仅审计。启动一次性迁移幂等删除 DB `global_settings` 里残留的 `skill_sources` 文档（保留 `skill_lock`）。
- dev 侧 worker 配置种子 `config/agent-worker.yaml` 与模板
  `config/agent-worker.example.yaml` 整体退役（issue #323）：worker 唯一
  生效配置收敛为状态副本 `data/agent-worker-service/worker.yaml`（控制台/
  API 驱动），消除「改了种子文件不生效」的双层配置漂移。`init-worktree.sh`
  / `install-deps.sh` 的种子逻辑改为直写状态副本，`worker.service --config`
  变为纯可选 bootstrap（仅 docker/远程 headless 部署使用，模板见
  `deploy/worker.*.example.yaml`），`make dev-up` 的 worker 启动闸门改判
  状态副本是否存在。
- openclaw runtime 整体退役（issue #75）：曾短暂经 catalog adapter 接入
  （`openclaw agent --local --json`），因其 stdout 只有一次性结果
  envelope——无流式事件、无 token 计量——按用户决策移除；agent runtime
  回到 pi / velites 两个。连带退役：实例设置 `openclaw` 块（存量 DB 文档
  读取时整块剥离、写入返回 422）、`AGENT_LEGION_OPENCLAW_CWD` env、
  `openclaw.cwd` 启动校验、Host 侧 openclaw agents 发现、Worker 侧
  openclaw 条目与模型发现 adapter。未来需要时按 adapter 机制重新接入
  （指南见 `docs/architecture/velites-harness.md`）。

## [0.4.0-alpha] - 2026-08-29

### Added

- `make install`: one-command setup for fresh clones — detects and (on macOS)
  installs missing prerequisites (uv, Python 3.11+, Node 18+, PostgreSQL 17,
  cargo, Docker), then runs `uv sync`, creates the database, generates `.env`
  with random local-RustFS credentials, builds the velites sandbox binary,
  installs frontend dependencies, and seeds the worker config and vault key
  (`scripts/install-deps.sh`, idempotent).
- Dev object storage works out of the box: `make dev-up` now starts the local
  RustFS container (via the existing `materials-local` compose profile, gated
  by `local-s3-decide.sh`) and ensures the bucket + CORS exist
  (`scripts/ensure-s3-bucket.py`, shared with `init-worktree.sh`). Switching to
  a cloud S3 is still just an `.env` edit — the local RustFS is then skipped
  automatically.
- Workflow definitions accept an optional top-level `execution:` block
  (provider/model/thinking) that the loader merges into every non-start
  node (node values win), versioned with the revision — one place to configure
  execution per workflow instead of per node.
- Studio node execution editor: provider/model inputs now offer runtime-aware
  suggestions aggregated from the workspace's online workers
  (`GET /api/workspaces/{id}/runtime-models`), with free-text fallback.
- Studio chat sessions can be resumed after close/error/backend restart:
  `POST /api/workspaces/{id}/studio-chat/sessions/{sid}/resume` respawns the
  ACP runtime with a fresh scoped token and rebuilds context via ACP
  `session/load` when advertised, otherwise by replaying a bounded transcript
  of the persisted history into the first prompt. The panel offers a
  「继续对话」 action and remembers the last selected session per workspace.
- Studio start-node contract editor rewritten in user-facing terms: each
  accepted item type (上传文件 / 外部平台内容 / 整个文件夹) carries a label
  plus a one-line scenario description, shared with the read-only view and the
  AddItemsDialog banner (internal jargon like `accepted_item_types` removed).

### Removed (workspace settings retirement)

- Workspace Settings「Agent 默认配置」(`default_agent_provider/model/thinking`):
  the provider/model/thinking resolution chain is now node `execution.*` →
  workflow-level `execution` default → actionable error; the three columns are
  dropped in schema v64 (cleanup-phase drop after the v62 replay, per the
  `cms_config_json` precedent). New manifests no longer bake
  `execution_defaults`; claim re-resolution stays tolerant of legacy in-flight
  manifests.
- Workspace Settings「接入与资源」 intake-mode toggles: item types are
  declared solely by the start node's `accepted_item_types` in Studio; the
  legacy `/job-batches` API is no longer gated by enabled intake modes. The
  default entity type (entityType) survives and moved into「基础信息」.

### Removed (dead code and stale artifacts)

- Removed the dead `server/app/services/vault_resources.py` module: zero
  importers and unimportable since the resource-providers retirement (a prior
  removal in PR #172 was reverted wholesale by `b9a35ff1`, which restored the
  file; the CHANGELOG had kept claiming it was gone).
- Removed the dead `server/app/services/token_usage_capture.py` wrapper (its
  only caller, `pi_runner.py`, was deleted earlier; the lease-scoped
  replacements in `token_usage_lease.py` remain) and the orphaned
  `server/app/executors/agent_workspace.py`.
- Removed retired/unused config surface: the dead `PiRuntimeConfig` block
  and the unconsumed OpenClaw runtime knobs (`command_template`,
  `timeout_seconds`, `isolated_workspace_root`, `skill_safety`) — the admin
  instance-settings `openclaw` document is now `cwd`-only. Stored documents
  from older deployments are normalized at read time (retired keys stripped
  before response validation, no data migration needed), and
  `openclaw.skill_safety.repos[].ref` stays rejected at startup (config
  governance G3: refs are pinned by the DB `skill_lock` document only).
- Worker: removed the test-only `read_current_executions` compatibility
  helper and `strip_secret_config` (never called on the Worker — secret
  stripping happens Host-side in `split_manifest_config` before dispatch;
  verified no caller in repository history).
- Frontend: removed the orphaned video-hive player cluster
  (`VideoPlayer`, `InteractionOverlay`, `SubtitlePanel`, `NodePanel`,
  `videoNodeStore` and friends, ~1,030 LOC) plus `CollapsiblePanel`,
  `TimelineStrip`, `materialWeb.ts`, and the superseded
  `getFilterCounts`/`filterCountsCore` pair — all unreferenced since the
  react-query migration; pruned dead exports in `labels.ts`/`theme.ts`/
  `nodeCatalog.ts`/types, dead rules in `styles.css` (634 → 118 lines) and
  seven CSS modules; moved `@tanstack/react-query-devtools` to
  `dependencies` (it is imported by the production entry), moved
  `@types/dagre` to devDependencies, and dropped the redundant
  `@types/katex` shim (katex bundles its own types). The filter-count
  exclusion semantics (each dimension counts jobs matching the other
  filters while excluding its own) and the worker status-reader edge
  cases (dead writer, corrupt/missing file, started_at ordering) were
  re-homed onto the surviving `computeFilterCounts` / `read_runtime_status`
  implementations with ported tests.
- Removed one-off scripts whose retirement conditions are met:
  `backfill_workflow_revision_resources.py` (schema has moved v26 → v58 and
  the loader hard-rejects the `resources` field), `bench_gzip_exemption.py`,
  `velites_replay.py`, `velites_diff_events.py` (rollout archived),
  `backfill_failure_classification.py`, `backfill_worker_output_validation.py`,
  `migrate_job_dirs_to_shards.py` — each with its unit tests.
- Docs/deploy hygiene: `.env.example` and the READMEs no longer instruct the
  retired global worker-register-token setup (which now fails startup);
  `scripts/stack-prod-up.sh` drops the `agent_worker_register_token` prereq
  and the broken `funasr` warm-up block (the dependency left the image);
  references to the deleted `check-skills-shared.py` and the no-op
  `verify_specs.py` gate step are cleaned up.


- CSRF negative-path test: cookie-authenticated mutations without the
  `x-agent-legion-request` header are rejected with 403 (SECURITY-AUTH-001).

### Security

- Shared-database schema guard: `init_db` refuses to initialize/migrate the
  bare shared `agent_legion` database (the code-default DSN) unless
  `AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1` is set — prod launchers
  (native-prod-up.sh, deploy/compose.host.yaml) set it, while a misdirected
  process (worktree script without .env resolving the default DSN) fails
  with remediation instead of pushing unreleased migrations onto prod
  (2026-08-27: an export_openapi run applied v59-61 to the shared database
  this way). `scripts/export_openapi.py` additionally refuses to run at all
  against the shared database before the app is built.
- Skills runs dir (per-execution skill snapshots + cache locks) moved from
  `~/.agents/skills/agent-legion.runs` to a deterministic per-user OS temp
  dir (`agent-legion-skills.runs[-<uid>]`), overridable via
  `AGENT_LEGION_SKILLS_RUNS_DIR`: leaked snapshots no longer pollute the
  agent skills namespace, and the OS temp TTL backstops them. The temp root
  is created/validated with CPython tempfile trust rules (atomic `mkdir
  0700`; on reuse it must be a non-symlink directory owned by the current
  user, mode normalized to 0700) — closing pre-creation/symlink attacks on
  shared `/tmp`. The leak GC (see Changed) reuses the same validation, and
  the `.locks` dir is 0700 with symlink rejection (EXEC-SKILL-RUNS-SCRATCH-001).

### Changed

- Repacked the 19 underscore-prefixed private modules under
  `server/app/services/` into real subpackages (issue #199, completing the
  cluster-repack pattern proven by #191 and #234): `job_rerun/`
  (batch / by_failure_results / eligibility / preview / preview_checks /
  single / upstream_guard, plus the batch delete / run-to loops from
  `_job_batch_ops` as `batch_ops`), `ops_metrics/` (catchup / queue /
  queue_alert / runs / sampling / series / summary / workspace_sampling) and
  `failure_classification/` (markers / rules). Import sites were rewritten to
  the full new paths (no re-export facade). Each cluster's former flat entry
  module moved into its package: `job_rerun.py` and `failure_classification.py`
  became the package `__init__.py` (so `from server.app.services.job_rerun
  import JobRerunService` and the `failure_classification` attribute imports
  keep working unchanged, mirroring the #234 `status/` precedent), while
  `ops_metrics.py` became `ops_metrics/service.py` with `OpsMetricsService` /
  `Granularity` re-exported from the package root — a package shadows the
  same-named flat module, so keeping `ops_metrics.py` flat was not an option.
  Architecture baselines carry the old ceilings to the new path keys
  (file budgets via the #236 rename-floor rule; the service-data-boundary
  counts move as-is, with `job_rerun/__init__.py` newly registered at its
  observed bypass count).

- Repacked 21 of the flat `worker/` prefix-cluster modules into real
  subpackages (issue #234, mirroring #191 on the server side):
  `execution/` (heartbeat / lifecycle / prepare / run), `runtime/`
  (controls / models / preflight / setup), `upload/` (heartbeat / prepare /
  queue / scheduler), `host/` (client / status_sync / transfer),
  `artifact/` (download / upload), `registration/` (retry / token) and
  `status/` (the former `status.py` reporter as the package root, plus
  aggregates / reader). Import sites were rewritten to the full new paths
  (no re-export facade); `from worker.status import …` keeps working because
  the reporter now lives in `status/__init__.py`. Entry-point modules stay
  at the package root — `worker.service`, `worker.executor`, `worker.cli` —
  so the Dockerfile ENTRYPOINT, Makefile targets and
  `scripts/native-prod-up.sh` keep working; the `service` / `cli` clusters
  (`service_bind` / `service_models` / `cli_args`) stay flat because a
  `worker/<name>/` package would shadow the `worker/<name>.py` entry module
  and break `python -m worker.<name>` (Python resolves the package first).
  The workerctl standalone COPY is unchanged, and the worker image smoke
  import now covers `worker.upload.queue`.

- **Breaking (API consumers):** workspace id and workflow key are one
  identifier (schema v62, DB-WORKSPACE-KEY-BINDING-001): `POST
  /api/workspaces` now requires an explicit `id`
  (`^[a-z0-9][a-z0-9_-]{0,63}$`) that is bound to `default_workflow_key` at
  creation and immutable afterwards — `workflow_mode` and the
  `default_workflow_key` create/update fields are removed (422 on extra
  fields, 400 on any later key change), workspace creation no longer seeds
  the sample template (demo workspaces are provisioned by `make import-demo`
  / `scripts/seed_demo.py`), and the first-publish key adoption path is gone
  (mismatched draft keys are rejected with 422). The v62 migration renames
  existing workspaces to id == key (cascading `workspace_id` through every
  child table plus the FK-less `auth_scoped_tokens` and
  `ops_metric_samples`, fail-fast on id conflicts) and backfills
  never-published workspaces with key = id; `default_workflow_key` is
  deprecated as a separate concept pending full retirement (issue #211).
  Legacy workspace URLs change accordingly (e.g. `/workspaces/demo` →
  `/workspaces/education_video_problems_generation`).

- **Breaking (deployments):** the global worker register token is retired —
  registration uses workspace-scoped tokens only, issued per workspace in the
  admin UI (workspace 设置 → Agent 与 Worker, workspace is now mandatory at
  issuance) and managed in the Worker console's new "Workspace 访问" panel;
  leftover `AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE)` env vars or yaml
  `agent_workers.register_token(_file)` keys now fail startup with migration
  guidance (#35, schema v58).
- Worker registration presents all configured scoped tokens in one call
  (`X-Agent-Worker-Register-Tokens`); the Host resolves the union workspace
  scope, rejects the whole registration when any token is revoked, and returns
  per-workspace rows (id + name) so the console labels each token (#35).
- `GET /api/agent-workers?workspace_id=...` narrows to workers registered
  with that workspace's tokens; each workspace's settings page shows a
  read-only worker list, while legacy `[]`-scope (global-token) workers are
  admin-visible only until re-registered (#35).
- Compose stacks no longer mount `agent_worker_register_token`; workers get
  their scoped token via the console or `workerctl configure
  --register-token-file` (#35).
- **Breaking (deployment):** `server.app.main` no longer exports a
  module-level `app`; launchers must use the factory form
  (`uvicorn server.app.main:create_prod_app --factory`). Importing the
  module is now side-effect free — the `AGENT_LEGION_SKIP_MODULE_APP` env
  escape hatch is retired.
- Schema upgrades record one `schema_migrations` row per version and only
  run data migrations above `max(applied)`; legacy single-row installs are
  a no-op (DB-SCHEMA-001).
- Sandbox argv/env/read-roots construction and the registration protocol
  constants live once in `shared/` (imported by both Host and Worker),
  replacing the cross-side "keep in sync" copies; network opt-in is now
  strictly `is True` on the Worker path too (P-0.5 semantics).
- The workflow worker's mutable state moved from ~18 thread-private
  attributes (reached into by sibling modules) into an explicit
  `WorkflowWorkerState` container consumed as `worker.state.X`.
- Studio layout components consume `useWorkflowStudio()` through
  `StudioStateContext`/`StudioViewContext` instead of threading the whole
  ~35-field object as props through six layers; the fabricated
  `WorkflowDefinitionRecord` in job detail is replaced by a minimal
  `NodeCatalog` type.

- Skills runs dir leak GC: the sweeper thread now removes execution
  snapshot dirs older than 1h (mtime-based; `.locks`, non-directories and
  symlinks untouched) — a hard crash between snapshot copy and the
  finally-cleanup previously leaked the snapshot permanently. Deployments
  with per-process temp dirs (systemd `PrivateTmp`, or a host CLI sharing
  the skill cache with a containerized server) must pin
  `AGENT_LEGION_SKILLS_RUNS_DIR` to keep the FileLock domain whole.
### Added

- Service data-boundary ratchet (BOUNDARY-DATA-001): new services under
  `server/app/services/` must reach the database through the `JobQueries`
  facade; existing raw-SQL/DB-primitive counts are frozen in
  `config/architecture/service-data-boundary-baseline.json` and only
  ratchet down.

## [0.3.0-alpha] - 2026-08-25

### Added

- Workspace materials store: S3-compatible presigned direct upload (RustFS
  locally), content-addressed local material cache for sandboxed nodes,
  add-items dialog, demo workspace material seeding, and a storage readiness
  probe in `/api/health` plus a startup self-check (#141).
- Item-based run creation API `POST /workspaces/{id}/runs` with typed items
  (#141).
- Mandatory `type: start` entry node in every workflow DAG carrying the
  `accepted_item_types` entry contract; item types `material` and `ref`
  (#156, #161).
- `bundle` item type: a folder as a single item (`material_bundles`,
  manifest-referenced members, two-way delete guard, deterministic
  hardlink-tree materialization, bundle upload panel in the UI) (#156, #164).
- Job artifacts unified into instance object storage
  (`jobs/{workspace_id}/{job_id}/{name}` keys + `job_artifacts` manifest
  table, schema v54); the local job_dir is now an evictable cache (#160).
- Worker `max_code_concurrency` hot-reload via the console /
  `PUT /api/config` without restart (#123).
- `scripts/resume-workspaces.sh` (on-demand workspace scheduling resume) and
  `scripts/trim_terminal_code_manifests.py` (drain legacy code manifest
  rows).
- Optional bundled RustFS in prod-up (#150).

### Changed

- **Breaking (API consumers):** `job_batches` migrated to first-class `runs`
  (schema v53) (#141).
- Studio chat MCP loopback is served over an in-app streamable-HTTP endpoint
  (`/api/studio-agent/mcp`) with scoped, workspace-bound tokens and sliding
  TTL (#157, #158, #159).
- Worker artifact return goes through claim-injected presigned S3 staging;
  the local `/api/artifacts` CAS remains as the legacy fallback (missing
  upload specs, direct-upload failure, or crash recovery re-enters the old
  channel) (#160).

### Fixed

- `agent_execution_requests` TOAST bloat (#142): the queued kind='code'
  manifest persists only a lightweight `runtime_context` audit stub
  (job/workspace ids + `batch_id`/`batch_hash`); the full DB-derived payloads
  (job, workspace, intake batch, skill_versions) are rebuilt on the
  claim-response path in memory, never persisted. Terminal code rows are
  slimmed back to the stub automatically; `scripts/trim_terminal_code_manifests.py`
  drains legacy pre-fix rows (ops-side `VACUUM FULL`/`pg_repack` still needed
  to reclaim disk).
- Terminal-bundle reap moved off the startup-critical sweep (#139).
- init-worktree S3 bucket step silently skipped on bare `load_dotenv` (#163).
- Studio chat MCP loopback deadlock and message interleaving; fully async
  httpx (#157).
- Material delete guards and endpoint precedence (#151, #153).
- Materials & runs v1 follow-ups (#154, #155).
- Performance: trigger-maintained workspace job node status counts (schema
  v56, #121), forced index for expired node-run sweep page reads (#122),
  and per-pass claim-input memos in the dispatch path (#124).
- Artifact store durability (#168): rerun promotes now back up pre-existing
  authority objects and roll back on mid-batch copy failure; cache eviction
  re-validates the job state before every unlink; empty worker-reported
  `content_hash` registers the Host-computed digest; quality artifact
  contents read bounded streams instead of whole objects.

## [0.2.0] - 2026-08-20

### Changed

- **Breaking:** velites provider/model configuration now uses the runtime-owned
  `~/.velites/models.json` registry. Worker discovery fails closed when the
  registry, requested model, or referenced credential is unavailable.
- Worker model capabilities are runtime-scoped `(runtime, provider, model)`
  triples under protocol v3. Rolling upgrades must update the Host before
  Workers so an older Host cannot erase the runtime dimension.
- The Worker discovers models through each selected agent runtime and applies
  its local runtime-scoped allowlist instead of treating one static list as
  shared by all harnesses.

### Added

- Native OpenAI-compatible Chat Completions and Anthropic Messages provider
  drivers in velites, including tool use, streaming, usage accounting, and
  Anthropic extended-thinking continuation state.
- Secure Docker credential injection for environment references used by the
  velites model registry.

## [0.1.0] - 2026-08-19

Initial open-source release.

### Added

- Workspace-scoped DAG workflows: nodes declare business `capability` only;
  the authoritative definition is the workspace's active revision, published
  from Studio drafts.
- Batch job intake with workflow-defined intake modes.
- Pluggable agent runtimes: Pi CLI and velites (Rust harness with a
  pi-compatible event stream); per-agent `runtime` selection.
- Versioned external skills: `{repo, ref}` sources and pinned commit locks in
  the DB (`skill_sources` / `skill_lock`), managed via admin UI or
  `make skills-lock`.
- Local and remote execution: executor leases for local capacity; remote
  Agent Workers register over HTTP, claim executions (agent and code nodes),
  and upload artifacts.
- Real-time console: React SPA with live DAG view, SSE dashboard events,
  WebSocket agent status, run logs, artifacts, and token-usage statistics.
- Secrets vault: Fernet-encrypted workspace secrets and instance-level
  external service connections; configs carry `secret_ref` only.
- Multi-user access control: cookie sessions with CSRF guard, admin user
  management, per-workspace editor/viewer membership.
- PostgreSQL control plane (PostgreSQL 17) coordinating multi-process and
  multi-machine scheduling.
- Demo workflow `education_video_problems_generation` under `examples/`,
  runnable out of the box against a real LLM.
- Docker deployment stacks (`deploy/`) and remote worker deployment runbook.

[Unreleased]: https://github.com/LuciusCao/agent-legion/compare/v0.7.6...HEAD
[0.7.6]: https://github.com/LuciusCao/agent-legion/compare/v0.7.5...v0.7.6
[0.7.5]: https://github.com/LuciusCao/agent-legion/compare/v0.7.4...v0.7.5
[0.7.4]: https://github.com/LuciusCao/agent-legion/compare/v0.7.3...v0.7.4
[0.7.3]: https://github.com/LuciusCao/agent-legion/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/LuciusCao/agent-legion/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/LuciusCao/agent-legion/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/LuciusCao/agent-legion/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/LuciusCao/agent-legion/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/LuciusCao/agent-legion/compare/v0.4.0-alpha...v0.5.0
[0.4.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/LuciusCao/agent-legion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LuciusCao/agent-legion/releases/tag/v0.1.0
