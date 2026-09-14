# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) once 1.0.0 is released.

## [Unreleased]

### Fixed
- 发布钉点漂移（issue #504，PR #503 codex P2）：0.7.0 发布时
  `install-worker.sh` 默认版本停在 worker 0.6.1 / velites 0.5.0、独立
  部署 compose 的 GHCR 镜像默认 tag 停在 0.6.0，一键安装拿不到协议 v5
  批量心跳与最新 velites 打点。钉点补齐到 0.7.0 / 0.5.1；安装器默认
  值收敛为 `*_DEFAULT` 变量单点定义（usage 文案同源打印，消灭文件内
  重复）。

### Added
- 发布钉点门禁 `scripts/check_release_pins.py`（backend 静态轮，紧邻
  check_versions）：`install-worker.sh` 的 `WORKER_VERSION_DEFAULT` /
  `VELITES_VERSION_DEFAULT` 对齐 pyproject / velites 版本，
  `deploy/compose.worker.standalone.yaml` 与
  `compose.worker.pull.example.yaml` 的 GHCR 镜像默认 tag（各两处）对齐
  pyproject 版本；比较复用 check_versions 的 normalize（PEP 440 预发布
  与 tag 形归一等价），钉点缺失或形态被改按 fail-closed 报错。契约
  测试 `tests/scripts/test_check_release_pins.py`（8 例）。

### Changed
- 原生形态 velites 二进制收敛为 PATH 单一副本（issue #507）：解析顺序从
  「data/bin 自带副本优先、PATH 兜底」反转为「PATH 优先、data/bin 兜底」
  （`shared/code_sandbox.py::resolve_sandbox_binary` 与
  `worker/binary_resolution.py::resolve_binary` 同步反转；fail-closed 语义
  不变——两侧都找不到仍拒绝执行/拒绝启动，EXEC-CODE-003）。背景：data/bin
  副本由旧版 `make install` 一次性播种后无任何维护方，而 `make prod-up`
  刷新的是 PATH 副本——data/bin 存在即永久遮蔽，升级后 prod 静默跑旧
  velites（0.6.0→0.7.0 实测复现）。收敛后：`install-deps.sh` 不再
  `--dest data/bin` 播种（PATH 形态安装到 `~/.local/bin`），检测到存量
  data/bin 旧副本时打印删除指引（不代删）；data/bin 目录保留为 Docker
  形态的外挂注入点（compose bind mount `/app/data/bin/velites`），容器内
  PATH 上无 velites、兜底即命中，Docker 部署零变化。裸机部署迁移：升级后
  删除 `data/bin/velites*` 即落到被维护的 PATH 副本。

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

[Unreleased]: https://github.com/LuciusCao/agent-legion/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/LuciusCao/agent-legion/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/LuciusCao/agent-legion/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/LuciusCao/agent-legion/compare/v0.4.0-alpha...v0.5.0
[0.4.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/LuciusCao/agent-legion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LuciusCao/agent-legion/releases/tag/v0.1.0
