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

## Kimi V1 兼容边界

路径遵循 Kimi 的 `KIMI_SHARE_DIR`（默认 `~/.kimi`）、工作目录规范路径的
MD5 目录名和 ACP session id：`sessions/<cwd-md5>/<session-id>/tasks/`。
只读 `spec.json` / `runtime.json`，只接受 version 1、session id 与任务 id
完全匹配、root 所属的 agent 任务。不会扫描其他会话，不读任务输出，不修改
Kimi 的通知消费状态。symlink、过大文件、不完整 JSON 与未知版本被忽略。
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
