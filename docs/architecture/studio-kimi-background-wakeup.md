# Kimi 后台子代理完成接续（#806）

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
完全匹配、root 所属的 agent 任务。不会扫描其他会话，不读任务输出，不修改
Kimi 的通知消费状态。普通轮询忽略 symlink、过大文件、不完整 JSON 与未知版本。
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

## Quality Impact

回归覆盖无人追问的完成接续、终态去重、运行中延迟、取消/关闭/旧 runtime
守卫、token 失效、句柄拒绝、跨会话/子代理归属过滤、坏文件与符号链接拒绝。
通过临时目录模拟 Kimi V1 文件及真实 watcher 线程，不需要模型调用。
确定性测试直接驱动 cursor 和真实 ACP 队列：覆盖无轮询间隔的 cancel/rearm、
入队后取消、消费前 DB 故障/凭证失效、enqueue 异常、runtime 替换以及祖先目录替换。
`test_studio_chat_background_boundaries.py` 在真实消息提交和 ACP load 边界完成任务，
覆盖取消期终态、扫描失败后的人工交接/重试、再次取消、空/非空恢复基线和 load 回落。
`test_studio_chat_baseline_observation.py` 覆盖真实目录缺失、目录替换、部分元数据、
初始扫描重试以及失败时游标不发生部分提交；人工交接回归直接注入读取器的文件系统故障。
服务启动排空与 fatal consumer fencing 复用 #814 的真实 ACP/数据库回归。
