# Kimi 后台任务活动与完成接续（#772/#806）

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
| 服务 | 接受启动 → 封闭入口 → 排空在途启动 → 清理 | shutdown 快照之后不能再注册后继；详见 [服务生命周期](studio-service-lifecycle.md) |
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

## 活动可见性（#772）

同一个 watcher 将创建、启动、运行、等待审批、完成、失败、终止、丢失和
超时状态写入既有 `status` 会话流；现有 StatusLine 直接展示 detail。
每条含任务 id、描述、开始时间与耗时，终态附截断结果摘要。状态不变时
不反复刷消息。超过 120 秒无输出、心跳过期或已有任务状态无法读取时，
写入一次对应提示；恢复活动后再写运行状态。提示只是观察，不会把任务
擅自标记失败或完成。会话恢复后历史终态不重放，仍在运行的任务重新展示。

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
活动测试另外覆盖运行/等待审批、无输出/心跳过期/状态不可读与恢复、
agent/bash 终态、摘要截断、FIFO 不阻塞读取、错误元数据和写消息失败重试。
无 schema 与前端 transport 类型变更，不增加模型轮询或平台执行写面。

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
排队消息未投递，请重发」）；引擎崩溃使 runtime 拆除时暂存消息不再投递，前端按 #1028 的
既有语义在会话不再存活时标「未送达」。
