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
只读 `spec.json` / `runtime.json`，只接受 version 1、session id 与任务 id
完全匹配、root 所属的 agent 任务。不会扫描其他会话，不读任务输出，不修改
Kimi 的通知消费状态。symlink、过大文件、不完整 JSON 与未知版本被忽略。
从绝对路径根目录开始逐级以 `dir_fd` / `O_NOFOLLOW` 打开目录，后续枚举与文件读取
始终相对已持有的描述符；祖先或任务目录被换成 symlink 不会改道读取其他目录。
元数据只读普通文件，`O_NONBLOCK` 与打开后的 `fstat` 同时防护 stat/open 间的 FIFO 替换。
恢复会话时已有终态作为历史基线，不重复唤醒；恢复期间仍在运行的任务继续观察。

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
