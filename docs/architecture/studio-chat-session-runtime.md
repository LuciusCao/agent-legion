# Studio chat 会话运行时

Studio 内置 agent 对话（`server/app/studio_chat/`）的会话运行时现行契约，按生命周期分两层：

- **服务层**：`StudioChatService` 的 create/resume 准入、shutdown 排空与清理顺序（STUDIO-RUNTIME-001），见下文「服务启动准入与关闭排空」。
- **会话层**：Kimi 运行时的后台任务活动回执、完成接续、自发回合与入站排队（#772/#806/#938/#972/#1029），见「Kimi 后台任务活动与完成接续」及其后各节。

本文由 `studio-service-lifecycle.md` 与 `studio-kimi-background-wakeup.md` 合并而成（#1103）。

## 服务启动准入与关闭排空（STUDIO-RUNTIME-001）

`ServiceLifecycle` owns process-local startup admission for `StudioChatService`.
Both public create and resume entry points acquire a startup permit before
reading or mutating session state. The permit covers claim, old-generation
teardown, token creation, registration, handle start, readiness, compensation,
and final response publication. Its `finally` releases the permit on success
and on failure.

Shutdown atomically seals admission, waits for every admitted operation to
finish, and only then snapshots the runtime registry. No startup producer can
register after that snapshot. Admitted operations may finish successfully
while shutdown is waiting; shutdown then cleans their resulting runtimes.
Requests arriving after the seal receive a conflict before creating a row or
token. This is a drain protocol, not cancellation of in-flight database work.

The condition is held only for permit accounting and sealing. Waiting releases
it, and neither the runtime lock nor the registry lock is held while draining.
Readiness callbacks therefore remain free to finish startup. A separate lock
serializes shutdown calls through cleanup, so a second shutdown cannot return
while the first is still draining or cleaning up. Repeated shutdown is safe.

Runtime identity checks remain necessary for normal close/resume and late
callbacks. They do not replace the service-wide producer barrier: identity
checks prevent an old callback from destroying a successor, while the barrier
ensures shutdown includes every successor produced by an admitted operation.

Shutdown waits for admitted operations, including the existing ACP startup
timeout; it does not invent a timeout that would return with producers still
active. Database/network stalls can consequently delay shutdown. Existing
best-effort database cleanup behavior is unchanged: a failed status write is
logged and repaired by startup reconciliation, and failed token revocation is
logged with token TTL as the fallback.

### Quality Impact（服务层）

`tests/services/test_studio_chat_shutdown_races.py` pauses real create/resume
operations before token minting and after registration, then starts concurrent
shutdown calls. Success and injected startup failure cases assert rejection of
new admission, draining of existing work, empty registry, revoked tokens,
terminated handles, and absence of durable starting/running rows. Existing
admission, resume, generation-bound callbacks and fatal-loop tests retain their
separate per-runtime guarantees. The invariant is `STUDIO-RUNTIME-001`.

## 压缩窗口超时清理（#694/#1153）

`compact_timer.py` 只负责 arm/cancel；`compact_timer_fire.py` 在 runtime identity 与
`compacting_since` 代次校验后，于同一 runtime.lock 内执行 conditional DB clear。
clear 抛错时没有可用的完成证明，保留窗口和代次并以 0.1 秒间隔、最多四次同代尝试；
重试耗尽只停止 timer 并保留 traceback，不伪报已清理。re-arm 会更换代次并取消旧 timer，
即使旧 firing 迟到也不能清掉新窗口。clear 成功后才最终清内存 flag/since/timer；
后续通知或快照异常不回滚镜像，也不因提交点不明而盲目重复 `compact_timeout`。
STUDIO-RUNTIME-001 同时约束该恢复路径。

## ACP 进程/流退出与幂等终态（#1153）

`acp_consumer.py` 在每代 handle 的同一个 asyncio event loop 中同时观察 direct
child 的 `returncode` 属性与 ACP 0.12 receive task。属性轮询可取消，不消费 waitpid
状态、不要求 stdout/stderr EOF，也不扩大到 descendant；ACP 仍负责后续 Process.wait/kill。
receive task 的结束代表 SDK 已看到 stdout EOF / transport disconnect；监视器不再次读取 stdout，不消费协议帧。
startup RPC、between-turn queue.get、prompt 与 post-cancel grace 都与同一个 death
task 竞争。死亡已观察到时优先于 turn_end、timeout、journal 与 late callback；
普通 agent `RequestError` 仍是 per-turn error，连接失效不再回 idle。

`process_exit.py` 先把本 handle 的 prompt admission fence 掉，再经
`ServiceCallbacks.on_connection_lost` 在旧 runtime 上执行 `exit_settlement.py`。
该路径设置 `runtime.closed`、停止 background wakeup、使本代空轮重投失效，并按
runtime identity（进程内 generation）、request id、message id 结算。第一次 EOF
投影保留 registry 中的旧 runtime，让 resume 仍能 join 可能活着的 direct child；
SDK transport 的有界关闭与 `terminals.close_all()` 后，线程最后的 `on_exit` 再按
identity/closed CAS 拆除。runtime lock 不包住 join、wait 或 kill，旧 generation
的 exit/callback 不得清理 successor。

| 事件 | 会话与终态语义 |
|---|---|
| 正常 turn_end / turn error | 持久完成行后才结束本 turn 的 delivered 跟踪；普通 `RequestError` 记录 per-turn error 后回 idle，direct child 继续存活 |
| PromptWedgedError 等 fatal `on_error` | 保留原 fatal detail，关闭 turn/compaction/tool 状态但不清 delivered identity；同代 exit settlement 补 unknown |
| mid-turn process/stream death | 不写 turn_end；会话进入 `error`，可 resume；本代 pending permission 按 request id 写一次 `resolved` |
| between-turns process/stream death | 与 mid-turn 共用 fatal 路径，不等待 prompt/journal timeout；已完成 turn 的消息不补 unknown |
| 尚未 before_start 的 queued/held 消息 | 只按 message id 写一次 `queued_dropped`，明确表示未投递，可人工重发；不清除下一轮或 successor 的状态 |
| direct accept 或 queued_delivered 已成功、turn 尚未结束 | direct 与 queued 都写现有 `error` 状态行，携带 `message_id` 与 `outcome=unknown`；不得谎报未送达，也不得自动重发 |
| 显式 close / resume / shutdown | 沿用 closed CAS、显式权限拒绝和 close/join/kill fence，不写进程崩溃 error；close 赢得 death 竞态时不追加 crash 事件 |

`queued_delivered` 只证明 `before_start` 的 token lock + idle claim + 状态行已在
同一事务提交，不证明 `conn.prompt` 已执行，更不证明模型已产生结果。若该事务已
提交但调用方收到异常，delivery 先读持久证明再决定，禁止补一条虚假的
`queued_dropped`。unknown 消息会从 fallback resume transcript 中排除；session/load
也不会由后端自动重放该消息，用户需核对后显式决定下一步。权限 waiter 在人工回答、
timeout/cancel 与 durable resolved 之间也保留 request id，退出结算可覆盖这段竞态。
delivered identity 只能在完成证据持久化后清除：`on_turn_end` 先写 turn_end/timeout，
普通 per-turn error 先写 `message_id+outcome=failed`，再调用 `note_turn_closed`；若 append
失败则身份保留。fatal `on_error` 用 `clear_delivery=False` 只关闭 turn/compaction/tool
状态，原 fatal detail 不变，active id 留到同代 exit settlement 补 unknown。正常 fatal-between
已完成输入因已有完成证据而不会被误标。

退出投影先读时间线上的 request/message 终态，成功后才移除本代待结算项；append
或 snapshot 故障可在同一代重试，不重复追加。主 process-exit error 以本代
`runtime_exit_id` 去重，resume 后的下一代死亡仍有自己的事件；startup refusal、
wedged turn 等既有 fatal `on_error` 则保留原 detail，不被改写成第二次进程崩溃。
两次即时 EOF 投影尝试之外，线程最终 `on_exit` 仍走同一幂等入口。append 使用
live/status/error_detail 守卫，starting、closed 或 successor 拥有行时旧 generation
不再写入。以上是同代去重：它解决本进程内 fatal 重试与 ABA，不承担重启后的恢复。

后端非正常重启后，`restart_settlement.py` 独立按持久证据恢复，不依赖 runtime
内存。startup 用专用 `list_studio_chat_messages_forward` 以 `seq>cursor ORDER BY seq ASC LIMIT 500`
真正前向分页，500/501/1000/1001 边界都覆盖早期 pending/direct/queued/delivered；
共享 `list_studio_chat_messages_page` 继续保留 UI/API 的最新 limit 页语义，不能互相替代。
随后重建 direct/queued 的 queued→delivered→dropped/unknown/completed 状态：无 resolved 的 permission 补
`deny(via=backend_restart)`，未 delivered queued 补 `queued_dropped`，delivered
但没有完成证据的输入补 `outcome=unknown`。终态优先级为 completed > delivered > dropped：
legacy `queued_delivered→queued_dropped` 若没有完成证据，仍按 unknown 补齐一次，不能被后
dropped 覆盖；若已有完成证据，dropped/fatal 也不会追溯抹掉正常历史。

完成证据必须有持久 turn 归属。`turn_state.turn_completion_evidence` 在 events 写
turn_end/turn_timeout/per-turn error 前快照当前 turn：人工 direct/queued/empty-retry 写
`turn_origin=human` 与对应 `message_id`；background wake 的空文本 `open_turn` 写
`turn_origin=platform`，runtime 缺失或无法归类写 `unknown`。平台普通 failed 不携带
`message_id+outcome=failed`，不能伪装成人工失败。初始 direct 与后续人工 turn 使用同一规则，
不需要 `session_created` 或默认 human 特例。

重建时，turn_end/timeout 只有 `turn_origin=human` 且 message_id 等于当前 active 人工输入
才完成它；普通 failed 也只接受 human，或 legacy 已带 message_id 的可归属形态。无 owner 的
legacy turn_end/timeout、origin unknown/不匹配及平台终态一律不完成 active，保留 delivered
并禁止重放。这是旧数据的保守上下文降级，不声称能完美区分所有历史无 origin 后台事件。
fatal 与 unprompted 既有排除不变。正常 `session_resumed` 仍清除旧 active；即使其 append
失败后新 runtime/idle row 已存在，后续 background wake 的 platform 终态也不能完成旧输入。

`resume_context.py` 与 startup 共用 `_reconstruct`，不维护第二套证据状态机。fallback
transcript 对用户文本只接受 `completed`，因此 direct/queued delivered、unknown、dropped
和未投递 queued 都不能自动重放；正常有归属的 turn_end/timeout/普通 failed 用户与助手历史
仍注入，assistant 文本、截断和 one-shot marker 不变。该只读过滤也让
archive→hidden reap→unarchive→loadSession=False resume 无需在隐藏期补 unknown：unarchive
与 resume claim 之间不存在可授权重放的写入窗口，不需要新增跨事务锁。相比在 unarchive
后补终态，它既复用完成证据模型，也避免另一线程先 resume 的竞态。

正常可见 startup 的候选 SQL 与最终 live→error CAS 同时要求 `deleted_at IS NULL` 和
`archived_at IS NULL`，读取后才隐藏的可见 row 不被改写，消息 append 的既有守卫不替代
这条 row CAS。delete/archive stamp 后 close 前崩溃的 hidden live row 则走独立回收：
`hidden=True` 读取 hidden 候选，独立回收只对其中 live row 做最终 CAS，同时要求 hidden
与 archived_at 快照相等，成功后才 teardown 本代 runtime/token；只把 row 改为 error 以释放
create/resume 活跃配额，不写隐藏 permission/queued/unknown 或 error 消息。unarchive 后
快照不匹配即跳过，恢复 row 不被误改，重复 reaper/unarchive 不重复写事件。archived row
可在 unarchive 后 resume，由上述 transcript 过滤封闭模糊输入；deleted 是不可逆隐藏，
没有 unarchive 入口，既有 resume/spawn fence 不变。

每次 reaper 都重读持久终态，只补缺项；permission/drop/unknown/status 任一阶段
在提交后报错，下一次启动仍按 request/message id 跳过已落库行，不重复追加。
单 session 全部缺失终态写妥后才做 live→error；任一 row 失败会在其他 row 修复后
聚合抛出启动失败，阻止未结算输入提前 resume，下一次启动再按持久证据补齐。closed row
或已被 successor 接管的 starting row 不归旧 reaper 写入。同代与 restart 两条路径共用同一事件语义，
但不互相冒充：前者依赖 runtime/owner，后者只依赖持久 seq 与 id；无需新增 DDL。

清理对象仅限本 handle 拉起的 direct ACP child 与该 child 经 ACP 登记的 terminal；
不按 process group 清理 Kimi 内部的非 direct-child 后台任务。

## 有界 turn 与统一 TurnLease（#1153 A2）

`StudioTimingConfig` 是 frozen 的 per-handle 时序快照，在 `Settings → StudioChatService →
spawn_session_runtime → AcpSessionHandle` 创建链上注入；已有 handle 不因之后修改 env 或
重新加载 settings 而改变。仅提供 env-only 键，非法、非有限或非正数在 settings load 时
fail-fast，显式 YAML 不构成第二配置通道，也不新增 UI、route 或 instance-settings schema：

| env | 默认值 | 语义 |
|---|---:|---|
| `AGENT_LEGION_STUDIO_CHAT_PROMPT_TIMEOUT_SECONDS` | 3600 | 一次 ACP prompt 的明确总时长，不是无活动超时 |
| `AGENT_LEGION_STUDIO_CHAT_CANCEL_GRACE_SECONDS` | 30 | 总超时或用户主动 cancel 后等待 turn 结束的宽限 |
| `AGENT_LEGION_STUDIO_CHAT_UNPROMPTED_IDLE_TIMEOUT_SECONDS` | 900 | 自发 turn gate 的无进展释放时长，仅 drop held，不杀引擎 |
| `AGENT_LEGION_STUDIO_CHAT_CLOSE_GRACE_SECONDS` | 5 | close 第一段 join 宽限；kill 后第二段 join 仍为其 2 倍 |

直接 handle/prompt 测试仍可用参数或现有模块常量注入小值。startup 60 秒与权限 120 秒
不在本配置面内。正常长 turn 只受明确总时长约束，安静但未到期不会触发 fatal。
`turn_timeout` 文案按本 handle 的总时长格式化；tool_call keepalive 的续期阈值同样使用
本 handle 的总时长加既有 300 秒余量，不再绑定模块常量。

### 单一 lease 与状态机

direct accept、queued `before_start`、empty-turn retry 和 background wake 都在各自 DB claim
前创建唯一 `TurnLease`。它统一持有 admission 身份、runtime/turn owner、direct process、总期限、
cancel epoch/deadline 与 Timer 代次。direct/empty 的 accept 回调显式返回新 lease，queue 的
`QueuedPrompt` 显式携带它；尚在 background turn 后排队的普通人工消息不继承 background lease，
其实际 `before_start` claim 返回自己的 lease 给同一个 prompt-start gate。`open_turn` 只激活传入的
同一 lease 并维护 M19 镜像，不再创建匿名 owner 或第二套 watchdog。CLAIMING→OPEN 激活先于
`runtime.turn_open=True` 镜像；有效 OPEN lease 自身即证明活跃，Timer 不得把这个合法窗口当作
stale/inactive 而丢弃当前代 deadline。

| 状态 | 含义与合法迁移 |
|---|---|
| `CLAIMING` | lease 已创建，DB claim/open 尚可暂停；可 `OPEN`、`ABORTED`、`CLOSED`，cancel 可直接 arm |
| `OPEN` | 已接受并激活总期限；可 `RESPONDED`、`CLOSED`，可进入 `KILLING` |
| `RESPONDED` | prompt response 已到，只撤销总期限；已有 cancel 继续计时，新 cancel 仍可 arm |
| `CLOSED` | 已取得正常 close 仲裁；append/proof 成功后保持，未提交失败回滚原状态，不得影响 successor |
| `ABORTED` | 明确 pre-claim 拒绝或持久证明未接受；恢复原 owner，不 kill，不遗留取消状态 |
| `KILLING` | 到期或 commit/open 结果不明时不可逆 fence；禁止新准入，立即 kill 已 pin direct child |
| `FATAL` | cleanup 与预准入 RPC 创建完成排序后的终态；禁止任何新 awaitable，进入 M19 settlement |

用户 cancel 在 service 入口记录 `requested_at`，先对 `active_lease` arm 并发送 `session/cancel`，
再做 wakeup/permission 等需要 `runtime.lock` 的工作。CLAIMING、OPEN、RESPONDED 使用同一个 lease；
首次 `requested_at` 决定首个 cancel deadline，重复 cancel 只增加 epoch，不滑动期限。没有活动 turn、
closed/aborted/fatal turn 都不保存“以后补偿”的取消记录，successor 从 epoch 0 开始。若失败返回时
已经 KILLING，任何 abort/close 都不得撤销 fatal fence。

DB claim exception 不等价于“未接受”。direct 只接受 claim 前 `seq` 之后的新非 queued 用户行，queued 与
empty-retry 也只接受各自 baseline 之后、事件与 message id 相符的交付状态行；证明存在时恢复同一 lease
和 open/queue 交接，不新写用户行、不自动重放。background 的现有 `claim_studio_chat_turn` 没有 attempt
身份，裸 running 不再是 accepted proof，commit 不明一律保守 KILLING，绝不能 OPEN+queue。只有明确
pre-claim rejection，或持久读取证明 session 仍 idle 且没有本次交付行时，direct/empty/background 才可
ABORTED，queued 才可按未投递 drop。证明读取失败、归属不明或 open 镜像失败同样选择保守 KILLING；
已知人工 message id 先保留为 delivered，再由同代 M19 恰好一次 unknown/settlement 或重启重建补齐。

queued 的 baseline 读取也属于队列所有权协议，在 `begin_turn_admission` 与 pending decrement 之后执行，
不得以 `after_seq=None` 继续 claim。baseline 读失败表示尚未尝试本次 claim：`queued_drop.drop_unstarted`
先尝试写既有 `queued_dropped`，只有 durable drop 才 abort lease 并释放 FIFO；drop 写失败则同一 lease
进入 conservative fatal，由 M19 按 queued 身份恰好一次补 drop。`inbound_pending` 与实际出队/终态一致，
后续消息不得越过尚未可见结算的旧消息。恢复期 cancel 不把它改成 delivered unknown；已有
`queued_delivered` 的输入仍走 unknown，不与 baseline 未开始 drop 混用。

prompt response 只把 OPEN 改为 RESPONDED 并清除 total deadline，不直接 CLOSED，也不清除 cancel。
清 total 后始终按剩余有效 cancel deadline 重建 Timer：更新 `deadline` 与 `reason`、递增 generation 并取消旧
Timer，首个 cancel deadline/epoch 不滑动；无 cancel 时则取消旧 Timer 且不重建。重复 response 不重复
rearm，旧 total-timeout Timer 不得越过剩余 cancel grace 错误 kill，正常 close/KILLING identity 不变。
`begin_normal_close` 在 control_lock 内验证 lease/runtime/owner/fence 后把 OPEN 或 RESPONDED 改为 CLOSED，
这是正常终态与 Timer/KILLING 的共同线性化点，不在锁内做 DB/I/O。response→cancel 只在该仲裁之前有效；
仲裁后不再接受新 cancel deadline，迟到 Timer 不写 unknown，KILLING 先发生则正常 append 不得开始。
正常 close 先取得 ownership，再在锁外 append、更新 turn_open 镜像与 session 状态。慢 DB 不重新开放
cancel；append 未提交则恢复原 OPEN/RESPONDED 与原 deadline，post-commit 按 baseline 后完整内容证明
完成，proof 不可读则回滚后保守 fatal。idle/publish 等后续失败也不能留下半应用 CLOSED：当前 attempt 的
正常完成证明交给 M19 终结为 error 而非 unknown，没有该证明才走 delivered unknown。

`request_stop()` 的 `_stop_requested` 只表示 graceful post-turn stop：禁止新 prompt/admission，但当前已绑定 lease
可在 response/普通 RequestError 后完成正常 close。consumer、PromptStartGate 与 begin_normal_close 共用
`prompt_can_finish` 判定，保留 `_closed`、fatal lease、transport failure、active lease/runtime/turn owner
校验，不把 graceful stop 当 fatal。token 失效已将 session 置 error 时，running/awaiting_permission→idle
的 guarded update 不覆盖它；正常 turn_end/failed 恰好一次，不写 unknown，startup reconcile 与 fallback
transcript 可使用该完成证明。KILLING/FATAL、transport failure 或真正不可结算 close 仍禁止正常 append。

direct 的 `delivery_seq` 是本次用户行 seq，queued/empty-retry 是本次 `queued_delivered` /
`empty_turn_retry` 状态行 seq。M19 exit settlement 只把 `seq > delivery_seq` 的 human turn_end/timeout
或 failed 视为本次完成；第一次 turn 的历史 turn_end 不得吞掉第二次 empty/queued delivery 的 unknown。
startup `_reconstruct` 继续按完整事件顺序处理 delivery→terminal，不引入第二套持久状态。正常取消完成后
人工 FIFO 不变，自动 wakeup 仍须后续人工消息 rearm。

### 锁与两阶段清理

| 锁 | 保护内容 | 到期路径是否等待 |
|---|---|---|
| `handle.control_lock` | active/fatal lease、状态、deadline、Timer 代次及身份复核 | Timer 只持此短锁；不等待其他锁 |
| `handle._state_lock` | accept/close、loop/conn/process 句柄 | Timer 与 pinned kill 不获取 |
| `handle.rpc_mutex` | `conn.prompt(...)` awaitable 创建与 KILLING→FATAL 的先后次序 | kill 不等待；仅 cleanup 获取 |
| `runtime.lock` | DB accept、M19 镜像、permission 与 exit settlement | fence/kill 不等待；仅后续 settlement 可等待 |
| `service._runtimes_lock` | runtime registry 与 successor 身份 | fence/kill 不等待；仅后续 registry 复核可等待 |

`PromptStartGate` 按 `rpc_mutex → control_lock` 做一次最终 lease/fence 判定，随后在 rpc_mutex 内
调用 `conn.prompt(...)` 创建 awaitable，锁外 await/schedule。KILLING 在 control_lock 内复核 lease、
runtime、turn owner、cancel epoch 与 process 身份并安装不可逆 fence，锁外立即 kill 该 direct
process；它不等待 rpc_mutex、gate done、runtime.lock、state lock 或 registry lock。

cleanup thread 再按 `rpc_mutex → control_lock` 把同一 lease 从 KILLING 写成 FATAL，然后复核
registry 并复用 fatal `on_error`、M19 `report_connection_loss_sync` / exit settlement 与
`teardown_runtime`。因此 KILLING 后最多当前已经持有 rpc_mutex 的预准入路径还能完成一次 awaitable
创建；它必须走 M19 unknown/settlement。即使该 RPC 在 cleanup 尚未写 FATAL/transport failure 时立即
返回 response 或普通 `RequestError`，consumer 也必须按 envelope 绑定的 lease KILLING/FATAL 状态跳过
正常 `on_turn_end/on_turn_error`，保留 delivered identity，不能回 idle 或把 unknown 改成正常完成。
FATAL 后不得再创建任何 RPC awaitable。这里的 RPC 起点是 `conn.prompt(...)` 返回 awaitable，不是
coroutine 首次执行，也不是底层 wire send；协议不宣称真实 wire 永不可能晚于 FATAL，只禁止 FATAL 后
新 awaitable，并保留已准入请求的 unknown 语义。

永久持有 rpc_mutex 时只能保证 KILLING fence 与 direct-child kill，不能声称 FATAL 已完成；永久持有
runtime/registry 等 settlement 锁时也不能声称 registry、DB 或 session 已完成。锁释放后，cleanup
与 M19 death task、`on_exit`、`on_connection_lost` 和显式 close 按同一 runtime 幂等完成，successor
不受影响。清理不消费 waitpid，不杀非 direct descendant；未进入 fatal 的普通 `RequestError` 仍走
per-turn containment，process/stream death 仍由 M19 立即接管。

### Quality Impact（有界 turn）

`tests/configuration/test_studio_timing.py` 覆盖默认值、合法 env、不可变快照、YAML 拒绝及
非法/非有限/非正数矩阵。强制交错矩阵如下；测试等待事件、持久终态或进程状态，不用固定 sleep
掩盖竞态。

| 编号 | 强制交错与不变量 | 主要证据 |
|---|---|---|
| T1 | 真实 `ServiceCallbacks + owned_callback` 持 runtime/state lock，仍先 fence+kill，释放后 settlement | `test_studio_chat_turn_watchdog.py` |
| T2 | response、cancel、close 的先后组合；response 只清 total，已有 cancel 不失效 | `test_studio_chat_turn_watchdog.py` |
| T3 | `queue.put` 内进入 fatal，direct/queued 都不得到达 `conn.prompt` | `test_studio_chat_turn_watchdog.py` |
| T4 | 持 `_runtimes_lock` 到期及 successor ABA；kill 不等 registry，旧 cleanup 不碰 successor | `test_studio_chat_turn_watchdog.py` |
| T5 | 最终 control check 后、awaitable 创建时进入 KILLING；当前 RPC 可完成，FATAL 后第二个不创建 | `test_studio_chat_prompt_start.py` |
| T6 | `conn.prompt(...)` 创建永不返回，kill 不等待 gate done/rpc_mutex | `test_studio_chat_prompt_start.py` |
| T7 | direct、queued、empty-retry 在 accept/open 前 cancel，同一 lease 按原时刻计时并结算 unknown | `test_studio_chat_prompt_start.py` |
| T8 | cancel 已读 lease 后暂停，分别先 arm 后 open、先 open 后 arm；无 stale pending、无 successor poison | `test_studio_chat_prompt_start.py` |
| T9 | accept/open 永久暂停至真实 grace 到期，CLAIMING 也 kill；释放后无 queue/RPC，仅一次 unknown | `test_studio_chat_prompt_start.py` |
| T10 | grace 前 claim rejection 为 ABORTED 且不 kill；KILLING 后 claim failure 不可逆 | `test_studio_chat_prompt_start.py` |
| T11 | 无 active/accepted turn 的 cancel 不毒化下一 prompt | `test_studio_chat_prompt_start.py` |
| T12 | 重复 cancel 增加 epoch 但不滑动首个 deadline | `test_studio_chat_prompt_start.py` |
| T13 | 旧 queue/response/timer 不得启动或解除 successor；queue envelope 必须携带原 lease | `test_studio_chat_turn_watchdog.py` |
| T14 | lease/runtime/process 身份复核；只 kill pin 的 direct child，不碰 replacement/descendant | `test_studio_chat_turn_watchdog.py`、`test_studio_chat_process_exit.py` |
| T15 | 普通 RequestError、未到期安静 turn、unprompted gate expiry 均不转 fatal/kill | `test_studio_chat_process_exit.py`、`test_studio_chat_turn_watchdog.py`、`test_studio_chat_unprompted_queue.py` |
| T16 | death、`on_connection_lost`、`on_exit`、close 与 off-loop cleanup 幂等；delivered=unknown、unstarted=drop | `test_studio_chat_process_exit.py`、`test_studio_chat_generation_boundary.py` |
| T17 | 真实 ACP 忽略 `session/cancel` 时，线外 grace 仍 kill 并停止后续队列 | `test_studio_chat_turn_watchdog.py` |
| T18 | direct/queued 的预准入 RPC 在 KILLING 后立即 response/RequestError、cleanup 延后；不写正常终态，仅一次 unknown | `test_studio_chat_turn_lease_recovery.py` |
| T19 | direct/queued/empty/background 在 activate 后、open 镜像前让真实 cancel Timer 到期；当前代 kill，successor 隔离 | `test_studio_chat_turn_lease_recovery.py` |
| T20 | direct/empty post-commit 证明恢复、background 无 attempt 证明保守 fatal、四路 open 异常；真正未接受才 abort，不自动重放 | `test_studio_chat_turn_lease_recovery.py` |
| T21 | direct/queued response/RequestError 在 consumer、真实回调入口、append→close 三处与 KILLING 交错；正常 close 先仲裁则正常完成，否则仅 unknown | `test_studio_chat_terminal_arbitration.py` |
| T22 | 第二次 empty/queued pre-claim、foreign running、append/idle/proof-read 失败及历史完成行；只接受本次 admission/complete proof，startup reconcile 幂等 | `test_studio_chat_admission_proof.py`、`test_studio_chat_terminal_arbitration.py` |
| T23 | 真实 prompt_loop 首次 baseline 失败→恢复→第二条发送、持续读/drop 写失败与恢复期 cancel；FIFO 不越过，queued 仅一次 drop，delivered 仅 unknown | `test_studio_chat_inbound_baseline.py` |
| T24 | total=10/grace=5，12s cancel→13s response→16s 慢终态不 kill，17s cancel deadline 清理；重复 response/close/旧 Timer 不影响 successor | `test_studio_chat_response_timer.py` |

真实 ACP 回归还证明普通 cancel 不杀健康进程、普通 RequestError 不转 fatal。测试另覆盖 direct
第二 turn、background 后 queued human 与 empty-retry 的 lease 显式传递，防止恢复旧的隐式 owner
推断。`turn_watchdog.py` 仅是现有调用面的 import facade；旧 candidate/pending 状态机和
`turn_watchdog_core.py` 已删除。

## Kimi 后台任务活动与完成接续（#772/#806）

Kimi 的 ACP `ACPSession.prompt()` 只在 prompt 请求内迭代通知流。Studio
在 idle 时保持 ACP 连接，并不能使 Kimi 开始消费已经生成的后台完成通知。

## 完成信号与生命周期

Studio 对识别为 Kimi 的运行时启动一个只读 watcher，每两秒检查该 ACP
session 的任务状态文件。没有后台完成任务时不请求模型。新终态先写入
`background_task_finished` 会话回执；空闲时经既有原子 turn claim 发出一次
系统提示，让 Kimi 消费原生通知、核对结果并汇报。正在运行或 compacting
时保留待接续任务，待空闲后合并为一次提示。系统提示不伪装成用户消息。

取消会话当前运行会禁用自动接续，用户再次发送消息才恢复。close、shutdown
和 runtime 替换停止旧 watcher。发送前复核 runtime 身份、关闭状态和 token；
失效 token 使用既有错误/恢复入口，不复活过期或撤销的凭证。

取消会递增 runtime 内保留的取消代次并清空 pending；人工消息持久化成功后，
在同一 runtime 锁内重新读取终态基线并启用接续。准备阶段只捕获代次，不缓存文件状态，
因此数据库提交期间新出现的终态也被纳入丢弃基线。扫描异常时保持关闭并登记重试，
不能中断已接受人工消息的队列交接；后续 watcher 先完成基线再恢复观察。新的取消会撤销重试。
排队的系统提示携带原代次，实际 ACP prompt 协程开始前再次校验代次、runtime、
compaction 和凭证。取消掉的队列项不调用模型；暂时无法校验凭证或正在 compacting
时释放本代 turn claim 并保留 pending。替换后的 runtime 不受旧队列清理影响。
凭证查询异常按失败处理，不能把通知链路 best-effort keepalive 的静默返回当成成功。
人工与自动 turn 各有独立的内存 owner 标记，清理必须同时匹配 runtime 和 turn；
因此旧自动队列项失效时不会把同一 runtime 的新人工 turn 改回 idle。清理数据库失败
由 watcher 重试带 owner 守卫的清理，不传播到缺乏 owner 的通用 ACP 错误回调。
正在 graceful stop 的 ACP handle 拒绝新提示，避免提示排在停止哨兵之后永远不执行。

## Kimi V1 兼容边界

路径遵循 Kimi 的 `KIMI_SHARE_DIR`（默认 `~/.kimi`）、工作目录规范路径的
MD5 目录名和 ACP session id：`sessions/<cwd-md5>/<session-id>/tasks/`。
只读 `spec.json` / `runtime.json`，只接受 version 1、session id 与任务 id
完全匹配、root 所属的 agent/bash 任务；只有 agent 终态参与自动接续。
运行中只取输出文件更新时间，终态最多读 `output.log` 末尾 2048 字节，展示
末尾 600 字符；有 `failure_reason` 时优先展示它。不扫描其他会话，不修改
Kimi 的通知消费状态。普通轮询忽略链接、过大文件、不完整 JSON 与未知版本。
基线扫描使用严格读取：根目录缺失、不可读、目录身份替换及任务元数据不完整均为
观察失败，不能作为空历史或部分历史提交；明确属于其他会话、其他类型或版本的任务仍排除。
从绝对路径根目录开始逐级以 `dir_fd` / `O_NOFOLLOW` 打开目录，后续枚举与文件读取
始终相对已持有的描述符；祖先或任务目录被换成 symlink 不会改道读取其他目录。
元数据只读普通文件，`O_NONBLOCK` 与打开后的 `fstat` 同时防护 stat/open 间的 FIFO 替换。
恢复会话在 resume claim 之前捕获已有终态，作为绑定路径和 ACP session id 的历史基线。
该快照穿过旧 runtime 清理、spawn 和 session/load，到 on_ready 初始化 watcher；
期间完成的任务不在历史基线中，继续产生回执与接续。空快照也是有效快照，不能在 ready 时重采样。
只有实际 session/load 成功且路径、session id 匹配才沿用基线；回落 session/new 使用新会话的就绪基线。
初始化或恢复时读取失败不影响人工聊天，watcher 保留未初始化状态并重试。首次完整扫描
成功后才允许接续；此前的完成项一并归入历史。因此历史不可读时可能不自动报告恢复期间
完成的任务，这是避免旧任务重放的保守降级。尚未创建 tasks 目录也属于未知，而非有效空目录。

## 组合状态模型

| 维度 | 状态/身份 | 负责的边界 |
|---|---|---|
| 服务 | 接受启动 → 封闭入口 → 排空在途启动 → 清理 | shutdown 快照之后不能再注册后继；详见上文[服务启动准入与关闭排空](#服务启动准入与关闭排空studio-runtime-001) |
| runtime | 注册对象身份，closed，ACP handle 停止状态 | 旧回调、旧 watcher、退出回收不得写入后继 |
| 接续 | enabled；disabled；disabled + rearm epoch | 取消立即生效，已接受的人工消息请求重新建立基线 |
| turn | owner 对象与入队时的取消 epoch | 队列消费前复核，旧队列清理不得释放新人工 turn |

| 事件 | 状态转换及观察顺序 |
|---|---|
| cancel | epoch + 1，disabled，清空 pending 与 rearm 请求；旧排队项在消费守卫中失效 |
| 人工准入失败 | 不改变接续状态、基线或 owner |
| 人工准入成功 | 提交消息 → 新 owner → 同代次 rearm → 新基线 → enabled → 队列交接 |
| rearm 扫描抛错 | 保留人工交接；disabled + rearm epoch；watcher 重试，新的 cancel 可撤销 |
| 新终态 | 回执成功后记入 seen/pending；忙碌或压缩时保留 pending，空闲后合并入队 |
| 自动队列拒绝 | 只释放同 runtime/owner 的 claim；同 epoch 且仍启用才恢复 pending |
| resume | claim 前历史快照 → 替换 runtime → load → 仅在同一已加载 session 上沿用快照 |
| close/退出/shutdown | 先隔离生产者/准入，再通知或清理，不能在终止标记之后继续生产消息 |

文件由外部 Kimi 进程写入，`runtime.lock` 不锁住外部写者。取消边界按新基线扫描中
实际观察到的有效终态定义，而不是声称获得整个目录的原子文件系统快照；逐文件观察后
才完成的任务属于后续观察。没有有效元数据的任务保持未知，不能据此推断它已完成。
扫描与启用之间不再插入数据库提交或其他异步等待。此桥接是进程内去重，不承诺跨进程崩溃的
exactly-once 模型调用，也不拥有 Kimi 的原生通知消费状态。

本桥接依赖 Kimi 的本地 V1 存储格式；远程 Kimi 或自定义外部存储不在支持范围。
若上游变更该格式，需要更新兼容读取器，不能把“读不到”当作任务已完成。
格式依据：[metadata.py](https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/metadata.py)、
[background models](https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/background/models.py)、
[ACP session](https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/acp/session.py)。

## 活动可见性（#772/#1152）

同一个 watcher 将创建、启动、运行、等待审批、完成、失败、终止、丢失和
超时状态写入既有 `status` 会话流；现有 StatusLine 直接展示 detail。
每条含任务 id、描述、开始时间与耗时，终态附截断结果摘要。状态不变时
不反复刷消息。running Bash 超过 120 秒无输出时仍写入一次 quiet，恢复输出后
再写 running。running agent 正常思考/写码不再只因 `output.log` 长时间未更新而
产生 quiet，也不会在下次输出时重复写 running；其创建、等待审批、心跳过期、
状态不可读和终态回执保持原有语义。两类任务的心跳过期或已有任务状态无法读取时
都写入一次对应提示，恢复活动后再写真实状态。提示只是观察，不会把任务擅自标记
失败或完成。会话恢复后历史终态不重放，仍在运行的任务重新展示。

回执游标与自动投递游标独立：取消和重启用只推进投递基线，不吞掉活动回执。
单个回执写入失败只重试该任务；旧 turn 清理失败只阻断自动投递，活动仍持续记录。
取消只停止自动接续，仍展示已派发任务的状态；Bash 任务展示状态但不会
触发 #806 的子代理自动接续。此适配支持本机 Kimi V1 与 Kimi Code（见下文 #972），
不推断其它 harness 的后台生命周期。不同 harness 需要各自提供有身份边界的真实状态来源。

## Quality Impact

生命周期修复遵循 `STUDIO-RUNTIME-001`：确认 token 失效后直接进入统一停止
路径，不再进行第二次存活探测，升级或通知写入失败也不能跳过本代 handle 的
非阻塞停止。关闭先在 runtime 锁内隔离生产者、拒绝已挂起权限，再写终止标记；
阻塞进程回收在锁外执行。普通 ACP 回调固定到创建它们的 runtime，权限等待
前后分别验证代次，等待期间不持锁，避免旧进程回声污染恢复后的会话。
回归覆盖升级/快照/通知故障、runtime 退出与关闭交错、终止标记顺序及迟到回调。

回归覆盖无人追问的完成接续、终态去重、运行中延迟、取消/关闭/旧 runtime
守卫、token 失效、句柄拒绝、跨会话/子代理归属过滤、坏文件与符号链接拒绝。
通过临时目录模拟 Kimi V1 文件及真实 watcher 线程，不需要模型调用。
活动测试另外覆盖运行/等待审批、心跳过期/状态不可读与恢复、agent/bash 终态、
摘要截断、FIFO 不阻塞读取、错误元数据和写消息失败重试。可控时间戳模拟一小时、
输出间隔 1–3 分钟的长任务，断言 agent 只有起始与终态回执，Bash quiet/恢复行为不变，
多任务去重互不影响，未 recorded 事件仍会重试。任务状态机、队列、进程控制与事件
schema 不变，持久化成功后才 recorded 的语义不变；无前端 transport 类型变更，
不增加模型轮询或平台执行写面。

## 恢复时补齐终态回执

生命周期消息携带 `acp_session_id`；恢复时按 chat session 与 ACP session
读取持久回执，并向前分页覆盖超过 500 条消息的会话。已报告运行状态、
尚无终态回执的任务若已结束，补写一次终态；已报告终态与未观察过的历史
任务继续忽略。旧消息缺少 ACP 身份时无法安全归属，不作为恢复证据。
读取历史或写入回执失败会重试，只有持久写成功才更新进程内去重状态。
恢复补报不自动唤醒模型，避免重启后丢失取消意图而恢复自主执行。
回归覆盖掉线期间完成、再次恢复去重、写入失败后恢复、跨 ACP 身份隔离。
确定性测试直接驱动 cursor 和真实 ACP 队列：覆盖无轮询间隔的 cancel/rearm、
入队后取消、消费前 DB 故障/凭证失效、enqueue 异常、runtime 替换以及祖先目录替换。
`test_studio_chat_background_boundaries.py` 在真实消息提交和 ACP load 边界完成任务，
覆盖取消期终态、扫描失败后的人工交接/重试、再次取消、空/非空恢复基线和 load 回落。
`test_studio_chat_baseline_observation.py` 覆盖真实目录缺失、目录替换、部分元数据、
初始扫描重试以及失败时游标不发生部分提交；人工交接回归直接注入读取器的文件系统故障。
服务启动排空与 fatal consumer fencing 复用 #814 的真实 ACP/数据库回归。

## Kimi Code 自发回合进入会话流（#938）

Kimi Code 0.43（`kimi acp`，agentInfo `Kimi Code CLI`）与上文的 kimi-cli V1 行为不同：
会话空闲时收到后台任务完成通知（`origin.kind = task`）或 cron 触发（`cron_job`），
引擎自己开一轮并调用模型；但其 ACP 适配层只转发绑定到在途 `session/prompt` 的那一轮，
其余回合的事件在发到 wire 之前就被丢弃。Studio 的 `on_update` 本身没有 turn 门槛，
SSE 与前端也始终订阅，问题在于这些更新从未到达。Kimi Code 会话上不发 #816 系统提示
（引擎自己开回合），任务回执改读 Kimi Code 布局，见下文 #972。

`unprompted_turns.py` 为识别为 kimi 的会话启动只读 watcher，每秒跟踪主 agent 的 wire
日志 `<home>/sessions/<workspace>/<acp-session>/agents/main/wire.jsonl`（`kimi_wire.py`）。
home 依次探测后端进程的 `KIMI_CODE_HOME` 与 `~/.kimi-code`：ACP SDK 以精简环境拉起子进程，
普通 `kimi acp` 只能用默认 home，包装命令另设 home 时需在后端进程设置同名变量。
从哪里开始读由「日志是谁写的」决定，而不是由某个时刻决定（任何时刻之前都还有时间，
那里写完的回合会被误当历史）：本 runtime 自己的 kimi 进程在 session/new 时创建的日志
（含恢复时 session/load 失败回落 session/new 的情形）整份属于本 runtime，从头读取，
Studio 发起的回合按 origin 跳过，不会重复；session/load 加载来的日志在 `resume.py`
里于旧进程被回收之后、新进程拉起之前取基线（`capture_wire_baseline`，只 stat 取身份与
末尾偏移，不读内容），此刻没有任何写者，新进程在 load 期间、on_ready 之前、首次轮询之前
写完的回合都落在基线之后；加载来的日志若没有可用基线（找不到、stat 失败、acp session
或路径不一致），则首次定位时取末尾，宁可漏报也绝不重放。文件被替换或截断时在新末尾
重新建基线，宁可漏报也不重复。逐级 `dir_fd` / `O_NOFOLLOW` 打开，日志本身经
`fs_safety.open_regular_at`（SECURITY-PATH-002）打开：只接受单链接普通文件，非阻塞，
不符合即拒绝且不读内容（#1044）。单次最多读 1 MiB，只消费完整行。

`turn.prompt` 的 origin 为 `user` / `skill_activation` 的回合由 Studio 发起（含 #816
系统提示），已经走 ACP，跳过；其余回合按到达顺序写入 `status`（`unprompted_turn` 回执）、
agent `text` / `thought`、ACP 形状的 `tool_call` / `tool_call_update`（id 为
`<turnId>:<toolCallId>`，与 Kimi ACP 一致），最后写 `turn_end`（`unprompted: true`）；
引擎以 `reason = failed` 结束的回合改写一条 `error` 状态事件（detail 只含错误 code 与
message，不含 details / cause），与 ACP 路径 `on_turn_error` 的形态一致，前端按既有告警条
显示，不写 `turn_end`。前端照常触发终止回取与草稿查询失效。写入在 runtime 锁内复核 runtime 身份与 closed，
关闭 / 删除栅栏之后不再写；写失败的行保留到下一次轮询重试，积压未落库期间暂停读取日志，
先按序持久化积压再推进，数据库故障期间内存积压不增长（#1044）。会话状态、turn owner
与 empty_turn 判定都不变；自发回合进行中用户发的消息由 Studio 暂存，见下文 #1029。
kimi-cli V1 会话没有该 wire 文件，watcher 保持空转。

## Kimi Code 任务存储布局（#972）

Kimi Code 把主 agent 的后台任务放在 wire 日志旁：
`<home>/sessions/<workspace>/<acp-session>/agents/main/tasks/<taskId>.json`（每任务一份
camelCase 信息文档，时间戳为毫秒，kind 为 `agent` / `process`），输出在
`tasks/<taskId>/output.log`；子代理只在结束时写输出，运行中的进度取其自身日志
`agents/<agentId>/wire.jsonl` 的修改时间。home 探测与 wire 日志相同（`KIMI_CODE_HOME`、
`~/.kimi-code`）。`kimi_code_tasks.py` 读取该布局，映射到与 V1 相同的任务快照（`process` →
`bash`，非 detached 的前台调用与其他 kind 忽略），继续走 `task_metadata_files` /
`fs_safety` 的逐级 `dir_fd` 打开与软失败策略；tasks 目录尚未创建视为空（首个后台任务时才建）。
`kimi_task_store.task_snapshots` 按根目录形状分派两种布局，恢复基线同样先探测 Kimi Code 会话目录。

会话是否为 Kimi Code 由会话目录是否存在决定：on_ready 时已能定位则直接按 Kimi Code
启动；尚未定位时按 V1 启动，V1 游标从未成功观察到根目录期间每次轮询重新探测，一旦出现
Kimi Code 会话目录即切换（kimi-cli V1 与 Kimi Code 的 ACP session id 不会互相命中）。
Kimi Code 会话只写 #772 活动回执，不发 #816 系统提示：引擎收到任务通知会自行开回合，
其内容经上文 wire 日志进入会话流。V1 布局行为不变。

## 自发回合期间的入站排队（#1029）

实测依据（Kimi Code 0.43.0 二进制内嵌源码）：ACP `session/prompt` 的忙检查
`assertNoActiveTurn` 只看 ACP 自己的在途 driver，自发回合没有 driver，检查放行；随后
`agent.prompt` 经 `AgentPromptChannel.submit` 把输入 FIFO 排进引擎队列，引擎处于
running 时直接返回 `undefined`（不等待启动），`driveLaunch` 据此立即以 `end_turn`
结算该 prompt，零内容。排队的输入随后作为 `origin: user` 的回合运行，ACP 已无 driver，
事件被丢弃；watcher 又按 origin 视其为 Studio 发起的回合而跳过。结果是回复在 Studio 里
完全不可见，empty_turn 的「继续对话」还会把同一句话再交给引擎一次。

因此 `unprompted_queue.py` 的 `GatedUnpromptedWatcher`（#938 watcher 的子类）在 wire
显示自发回合 open（见到 `turn.prompt`、未见 `turn.ended`）期间暂存人发的消息：消息照常
落库为「已排队」（#1028 的 `content.queued` 行），但不送 ACP；该回合的结束行落库后，
暂存消息按到达顺序交给 ACP 队列，走 #1028 的 `before_start` 投递（认领 + `queued_delivered`，
无法接收时 `queued_dropped`）。暂存期间后到的消息一律跟在后面，后台唤醒（`wake_session`）
让位，空轮「继续对话」返回 409 提示稍后再试。open 状态只在 projector 投影出的行全部落库后、
于 runtime 锁内采纳（与发送准入同一把锁），不会出现「回合已结束但结束行尚未落库」时放行。
发送准入前先同步推进一次 watcher，缩小每秒轮询的空窗。

出队同样重新观察日志（#1109）：一次释放多条暂存消息时它们同时进入 ACP 队列，引擎可能在
前一条结束、下一条开始之间（毫秒级、其间无轮询）因 task / cron 新开自发回合。因此每条排队
消息的 `before_start`（`prompt_turn.py` 把它放到工作线程执行，不在 ACP 事件循环里读日志、
写库）先自己推进一次 watcher（并发 step 进行中时最多等 `DEQUEUE_STEP_TIMEOUT_SECONDS`），
随后在 runtime 锁内裁决：门开着、已有更早的消息被退回、或等不到 step，就把消息退回暂存队列
（排在已退回的消息之后、新到的暂存消息之前），不发送、不写提示行，`inbound_pending` 照计；
step 本身失败则按已结算的门状态裁决（否则日志损坏时会来回弹跳）。暂存消息只在 ACP 队列里
已没有排队消息（`inbound_pending == len(held)`）时才整体释放，释放的消息不会排到更晚的消息
后面。`wake_session` 的认领发生在调用方持有 runtime 锁时，无法推进 watcher，于是在
`before_start` 这一实际发送边界同样推进一次：门开着、有暂存消息或等不到 step 时释放认领、
把任务 id 放回游标稍后重试，不吊销运行令牌。

残余窗口：引擎已开回合、但日志尚未写出（或刚写出、出队那次 step 恰好没读到）的瞬间出队的
消息，仍按 #1029 之前的行为送进 ACP，这是轮询日志这一观测方式固有的。回合永不结束时不永久卡住：日志被替换或截断（重建基线后
`turn.ended` 不可观测）立即解除，日志连续 `IDLE_TIMEOUT_SECONDS`（900 秒）无进展（含日志
读取持续失败）超时解除，两者都把暂存消息记 `queued_dropped`（「agent 自发回合长时间无进展，
排队消息未投递，请重发」）；引擎进程/流死亡时按 #1153 的本代 message id 结算，
未 before_start 的暂存消息明确写 `queued_dropped`，已 delivered 但未结束的写
`outcome=unknown`，不再只靠前端的会话存活兜底推导。
