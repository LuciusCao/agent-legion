# Agent Worker 架构：准入、velites 安置、控制面鉴权与控制台入口

本文收 Agent Worker 的设计与实现细节（#1103 自 [docs/agent-worker-deployment.md](../agent-worker-deployment.md)
迁出）。部署操作步骤、命令与排障仍以部署文档为准；协议版本、混合舰队兼容矩阵、结果上报格式与
**Host first, Worker second** 升级纪律的权威出处是
[remote-execution-runbook.md §5](../remote-execution-runbook.md#5-workers)；code 节点执行协议见
[node-sdk-and-worker-execution-design.md §7](node-sdk-and-worker-execution-design.md)，部署形态硬约束
（Worker 容器特权边界等）见 [deployment.md](deployment.md)。

## 1. runtime 声明与 agent 任务准入

Worker 不声明 `capabilities`（issue #284 起 claim 准入不再按 capability 匹配；
worker.yaml 的 `capabilities:` 键已于 issue #452 移除：存量状态副本残留该键时读取即
剥离、进程内告警一次，下次保存配置时从文件清除；`PUT /api/config` 与
`workerctl configure` 不再接受该字段）。`models` 是可选的 runtime-scoped allowlist，不再是
模型事实源。Agent runtime 声明由本机探测推导（issue #254）：启动时按二进制解析
（自带副本 `data/bin/` 优先、PATH 兜底）探测已安装的 runtime 并默认全部启用，
`disabled_runtimes`（控制台「配置 → Agent 运行时」或 `workerctl configure
--disable-runtime`）反选停用。Worker 对每个生效的 runtime 执行其发现 adapter
（velites 使用
`velites models list --json`），最终注册集合 = 发现结果 ∩ allowlist；该 runtime 没有
allowlist 条目时允许其全部发现结果。Agent 任务的准入条件：workspace token 授权、
runtime 匹配、provider/model 命中 allowlist、labels 满足 `requires_labels`。

## 2. code 任务的 secret 边界

**secret 边界**：节点 secret（vault 解出的连接凭据）只在 claim 响应里经既有 HTTPS 通道注入——落库的 manifest 与 bundle 都不含 secret；Worker 仅内存持有、经 stdin 传给沙箱子进程——secret 标记键在 Host 侧 `split_manifest_config` 就不进下发 manifest，Worker 侧没有任何 config 派生数据落盘，secret 不接触 Worker 文件系统与日志。随 manifest 下发的 settings 快照按 section 白名单过滤（`node_safe_settings_config`）——白名单当前为空（`NODE_SETTINGS_CONFIG_SECTIONS = ()`，业务 section 已随业务节点迁出），vault/auth/database/agent_workers 等实例级 section 不落库、不下发、不进沙箱 stdin。

## 3. velites 安置点与升级语义（#831）

**升级语义（#831）**：原生形态 `make prod-up` 对 velites 的两个安置点**都**做新鲜度刷新——先 PATH 模式（`ensure-velites.sh` 默认模式），再 `--dest data/bin`（自带副本，各自按指纹独立跳过/重建）。历史版本只刷 PATH，而「自带副本优先」的解析顺序让 install-deps 首次安置的 `data/bin` 副本永久优先命中，velites 升级静默失效。「安置在哪、什么算新鲜」由部署 planner（`scripts/velites_deploy_plan.py`）从**真实 resolver**（`worker/binary_resolution.py` / `shared/code_sandbox.py` / `worker/runtime/catalog.py`）推导，脚本只做构建与原子安置——bash 不再持有平行的查找模型（四轮 codex 同构 finding 的根源）。PATH 模式的主目标跟随 `which velites` 就地刷新（无 PATH 副本时落 `VELITES_INSTALL_DIR` 或 `~/.local/bin`），并对 PATH 上按名独立发现的家族成员位置（`velites` 与 `velites-sandbox` 可来自不同目录，#835 codex P2）一并刷新。沙箱包装器 `velites-sandbox` 同批刷新（解析候选序它优先于 velites，目录里存在旧包装器时只刷 velites 会让 code 沙箱继续用旧版——与 #831 同构的漂移；判鲜是**家族级**的：velites 本体全新鲜但包装器陈旧同样触发重建，fast-path 不会短路掉它；不存在时不主动创造，裸机默认走 velites 兜底）。重建需要 cargo——新 worktree 的 `data/` 为空，首次 prod-up 必经 `--dest` 构建路径，无 cargo 即 fail-fast（错误提示给出合法出路：装工具链，或从同 OS/架构、同仓库状态的机器连 stamp 一起拷贝；不得给 Release 产物手写当前指纹，见部署文档 §5「velites 二进制来源」）。启动对账（软告警）覆盖**每个消费角色实际解析到的**二进制：Worker 侧组合 agent runtime 面（自带副本优先解析）与 code 沙箱面（`velites-sandbox` 优先候选序）；Host 侧 lifespan 也对 code 沙箱面（经 `shared` 解析）与本地 agent runtime 面（PATH 裸名）对账——`data/bin` 副本漂移时两侧启动日志都打 WARNING。方向中性（副本可能落后于源码，也可能来自领先版本线的 PATH 共享副本；不 fail-closed，velites 版本线独立，允许刻意落后；无 stamp、stamp 损坏或无 git/源码树的形态无从对账，静默跳过）。对账核心在 `shared/velites_staleness.py`（Host 侧钩子不 import worker 包，两侧共用）。

## 4. 控制面鉴权的判定模型

操作步骤（取 token、`workerctl` 的 token 来源顺序与 `--state-dir`）见部署文档 §5「控制面鉴权」。

**页面内嵌判定（issue #489）**：控制台页面是否自动内嵌 token，由**实际暴露面**而非进程 bind 决定。Docker 形态下容器内进程必绑 `0.0.0.0`（端口映射前提），但页面真正从哪个地址被访问由 compose 的宿主侧发布地址（`AGENT_WORKER_UI_BIND`）决定——compose 把该值经 `AGENT_WORKER_UI_EFFECTIVE_BIND` 环境变量告知 service（与发布行同一插值源，`.env` 一处改、两处同步）。该机制随 **worker 0.7.16** 发布，且以下方「Host 头校验」已启用为前提：一键安装（`install-worker.sh`）在更低版本上安装时页面仍要求手动输入 token（脚本的成功提示会按实际版本区分）。判定矩阵（进程 bind × 宿主侧发布地址 × token 内嵌结果）：

| 进程 bind（`--host`） | 宿主侧发布（`AGENT_WORKER_UI_BIND`） | token 内嵌 | 说明 |
| --- | --- | --- | --- |
| `127.0.0.1` 等回环 | （无发布层，裸机/dev 形态） | 是 | `AGENT_WORKER_UI_EFFECTIVE_BIND` 未设置，按进程 bind 判定 |
| `0.0.0.0`（容器内） | `127.0.0.1`（默认） / `[::1]` | 是 | 容器内 bind 仅为端口映射前提；宿主发布回环 = 页面仅本机可达，内嵌不扩大风险面，日志打 info 说明判定链。compose.host.yaml 形态另需网络隔离成立（见下方网络拓扑） |
| `0.0.0.0`（容器内） | `0.0.0.0` / `192.0.2.1` 等非回环 | 否 | 同网段浏览器都能打开页面，不内嵌 + warning，需手动输入 token（见部署文档的取 token 命令） |
| 回环 | 非回环 | 否 | 复合形态兜底：设置了 `AGENT_WORKER_UI_EFFECTIVE_BIND` 时判定只看发布面（发布非回环即不内嵌，覆盖发布层与进程 bind 不一致的场景） |

**Host 头校验（issue #923）**：控制面全部路由（`GET /`、`/assets/*` 与全部 `/api/*`，含 `/api/health`）只接受 Host 头属于白名单的请求，其余一律 403。白名单 = 回环变体（`127.0.0.1` / `localhost` / `[::1]`，任意端口）∪ 实际暴露面地址（`AGENT_WORKER_UI_EFFECTIVE_BIND`，未设置时取进程 bind）∪ `AGENT_WORKER_CONSOLE_URL` 的主机名。经主机名（反向代理、MagicDNS 名等）访问控制台时，把该地址写进 `AGENT_WORKER_CONSOLE_URL`。白名单中只要出现非回环主机名（暴露面或控制台地址），页面就不内嵌 token，需手动输入一次。暴露面为通配地址（`0.0.0.0` / `::`）时无法枚举合法主机名，Host 校验不启用、页面也不内嵌 token（API 仍由 control token 把守）。变更类请求（`PUT` / `POST` / `DELETE`）另做来源校验：浏览器带 `Sec-Fetch-Site` 时只放行 `same-origin` / `none`，带 `Origin` 时须与 Host 头一致或等于 `AGENT_WORKER_CONSOLE_URL` 的 origin（反向代理改写上游 Host 的形态）；`workerctl` 等不带这两个头的客户端不受影响。

**网络拓扑差异（`compose.host.yaml` 形态）**：部署机 stack 里 postgres / seaweedfs / rustfs / host / worker 原本同挂一个默认 compose 网络——而 worker 控制台 `GET /` 无鉴权，同网 peer 容器 `curl http://worker:8787/` 即可提取内嵌的 control token（issue #489 讨论里担心的「token 泄给同网段」在 docker 内网上被重新引入）。该 compose 现已把 worker 隔离到专用 `worker-ctrl` 网络：host 双挂默认网络与 `worker-ctrl`（仍是 worker 唯一需要直连的 peer——register/claim/heartbeat/result 与旧 CAS 产物通道都走它），worker 只挂 `worker-ctrl`，postgres/seaweedfs/rustfs 不可达无鉴权的控制台。worker 的材料下载与产物直传本就走 Host 按 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`（宿主发布地址，非 compose 服务名）签发的 presigned URL 且 worker 不持对象存储凭据，网络隔离不改变该通道；但把 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 覆盖为 compose 服务名（如 `http://seaweedfs:8333`）在这种形态下会不可达——产物直传自动回落经 host 的 CAS 通道，材料任务会失败，覆盖值必须用宿主侧发布地址。无 peer 的 `compose.worker.yaml` / standalone 形态不需要（也从未需要）该隔离。

裸机/dev 形态不设 `AGENT_WORKER_UI_EFFECTIVE_BIND`，按进程 bind 判定。

## 5. 「打开 Worker 控制台」入口的实现约束

部署级入口（`AGENT_LEGION_WORKER_CONSOLE_URL`）与 Worker 自报入口（`AGENT_WORKER_CONSOLE_URL`）的配置方式见部署文档 §5「打开 Worker 控制台」入口。

- **标签保留**：已配置的 `console_url` 与其他自定义标签始终原样保留（可能用于 `requires_labels` 调度），环境地址不覆盖它；自定义标签已占满 32 项或自报 URL 超过 256 字符时跳过该可选标签，避免控制台入口使注册失败（不截断 URL）。
- **地址校验**：非空配置必须是绝对 HTTP(S) 地址，支持 IPv6、反向代理路径与 query；空白、反斜杠、非法端口或 URL 用户名/密码在创建服务前报错，诊断不回显原值。地址是公开导航信息，控制令牌在 Worker 控制台中输入，不放进该地址。
- **前端行为**：入口通过已登录用户可读的 `GET /api/agent-workers/console` 获取部署地址，不下载 Worker 清单；首次请求失败显示可重试错误，只有成功返回空地址才表示未配置；后台刷新失败保留最近成功的配置。

## 6. 领取状态与引导 / 排查横幅的判定

新 workspace 引导只有在 workflow 已发布、所需 Worker 已就绪且调度确认运行后才解锁添加任务。暂停状态未加载或请求失败时不推断为暂停，也不显示确定性的阻塞警告；纯 code workflow 不要求接入 Worker，只检查调度开关。

领取状态属于当前注册凭据：重注册生成新 token 时清回 `null`，旧 token 的在途 presence/claim 请求不能改变新注册的状态或在线时间。presence 与 claim 在写事务中锁定当前 Worker 行并复核凭据，状态未变化时也必须完成这一步。

引导只在空态可见时请求准备状态；等待任务的排查横幅按需请求。首次未返回或刷新失败都视为未知，不据此宣称已暂停、无 Worker 或可执行；正常后台刷新保留上次成功快照。排查入口优先使用当前在线且未撤销的 Worker 地址，暂停／恢复失败会明确提示并允许重试。
