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

取消会递增 runtime 内持久保留的取消代次并清空 pending；重新启用前在同一锁内
重新扫描终态基线，即使两次轮询之间发生取消和重发，也不会接续取消期间的完成项。
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
只接受 version 1、session id 与任务 id 完全匹配、root 所属的 agent/bash
任务。只读 `spec.json` / `runtime.json`；运行中只取输出文件更新时间，
终态最多读 `output.log` 末尾 2048 字节、展示末尾 600 字符作为摘要，
有 `failure_reason` 时优先展示它。不会扫描其他会话，也不修改 Kimi
通知消费状态。symlink、非普通文件、过大/不完整 JSON 与未知版本被忽略。
从绝对路径根目录开始逐级以 `dir_fd` / `O_NOFOLLOW` 打开目录，后续枚举与文件读取
始终相对已持有的描述符；祖先或任务目录被换成 symlink 不会改道读取其他目录。
元数据只读普通文件，`O_NONBLOCK` 与打开后的 `fstat` 同时防护 stat/open 间的 FIFO 替换。

恢复会话时已有终态作为历史基线，不重复唤醒；恢复期间仍在运行的任务继续观察。

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
触发 #806 的子代理自动接续。此适配仅支持本机 Kimi V1，不推断其它 harness
的后台生命周期。不同 harness 需要各自提供有身份边界的真实状态来源。

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
