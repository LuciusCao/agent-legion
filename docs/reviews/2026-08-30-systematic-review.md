# Agent Legion 系统性 Review（develop @ ba7ecc4b，2026-08-30）

> 方法：4 个并行深度排查（后端架构/安全/性能、前端架构/性能/类型、Worker+Velites+部署、测试质量审计）+ 本人复核（静态工具全量运行、三套测试套件实跑、CI 历史与覆盖率核验、上次 2026-08-26 review 遗留项逐条对账）+ 关键发现抽样核验源码。

## 总体结论

**这是一个工程质量显著高于行业平均的代码库，未发现任何 P0 级问题。** 核心风险不在代码内部质量，而在四个「架构假设/单点」：单进程事件面、容器特权组合（沙箱逃逸=宿主 root）、零 HTTP 层可观测性、无前端 ErrorBoundary。每一项都是一个 PR 量级的修复。

**实跑验证结果**（develop worktree）：

| 检查 | 结果 |
|---|---|
| ruff check / format（1206 文件） | 全绿 |
| mypy（621 文件） | 全绿 |
| tsc --noEmit + eslint + prettier | 全绿 |
| 后端单元层（2085 collected） | 2083 passed / 1 环境性 flaky（SIGPIPE，隔离重跑通过） |
| Postgres 层抽样（tests/db/test_jobs.py） | 13 passed |
| 前端 vitest（202 文件） | 1477/1477 通过 |
| velites Rust（release） | 158 passed |
| 架构不变量（73 条）+ 豁免（11 条）+ 文档新鲜度 | 全部通过 |
| CI（最近 develop run） | 13 个 job 全绿；合并后端覆盖率 94%（地板 85%）、前端 lines 87.4%（地板 86%） |

## 一、项目概况与架构

**规模**：后端 Python ~59K 行（server 53.5K / worker 5.4K / shared+workspace_libs ~1.6K），前端有效源码 ~35K 行 TS（另有 11K 生成代码 + 36K 测试），Rust（velites）~10.6K 行，测试 ~93K 行 Python。单人项目（近 30 天 964 commits），v0.4.0-alpha。

**四件套架构**：

- **Host（server/）**：FastAPI 组合根显式装配（`RouterDeps` 22 依赖 dataclass）→ routes（43 个 `*_contracts.py` Pydantic 契约文件）→ services（构造器注入）→ JobQueries 门面（23 mixin 组合）→ db（raw SQL + psycopg3 池）。横向子系统：agent_broker（3.7K 调度）、workflow_worker（2.9K 轮询+wakeup 混合调度）、studio_chat（ACP 子进程）、events（SSE+WS）、mcp_server。
- **Worker（独立进程）**：FastAPI 本地控制面 → supervisor（指数退避监督）→ executor（claim 主循环）→ 沙箱执行 → 双车道上传（marker-first 持久化语义）。
- **Velites（Rust）**：agent harness + 沙箱后端（macOS seatbelt / Linux bubblewrap），与 Python 无 FFI，纯子进程协议边界。
- **前端**：React 18 + MUI + TanStack Query（服务端状态）+ zustand（SSE 物化视图/UI 态）+ XYFlow/dagre（DAG）。

**架构判断：健全。** 依赖方向经实测干净（services 零 import routes、db 零 import 上层、SCC 环检测通过）；「治理即代码」体系（1406 条文件体积预算棘轮只降不升、73 条不变量注册表带 evidence+owner+gate、service-data 边界基线、SQL 占位符基线）是同类项目中罕见的工程化水平，且实测全部通过——不是摆设。

## 二、代码质量

**强项（有实测数据支撑）**：

- 全库仅 **1 处 TODO**（模板文件的示例占位）；前端零 TODO/FIXME、零 `any`（82K 行中 2 处命中均为注释文字，ESLint error 级强制）。
- 无千行级上帝文件：后端最大 547 行、前端最大 541 行，且超预算文件全部走豁免流程登记。
- 错误处理统一：领域异常族 + `raise_job_http_error` 集中映射；129 处 broad-except 均带「为什么安全」注释（#204 审计）。
- 注释文化：issue 编号、事故日期（2026-08-18/08-27）、性能数字（「25.9 万 job 30s」→优化过程）写进注释，是决策日志而非复述代码。

**主要问题**：

| 问题 | 位置 | 严重度 |
|---|---|---|
| service 层返回裸 `dict[str, Any]`（~1129 处 Any），Pydantic 类型安全在 service 层归零，字段拼写错误只能靠测试兜 | services 全层（典型 `token_usage_workspace.py:361-402`） | P2 |
| `Any` 充当 DTO 使 mypy 全绿含金量打折（「从未检查」而非「绕过检查」） | 同上 | P2 |
| `_timestamp` 辅助函数 6 处复制；AuthError/JobServiceError 两套异常→HTTP 映射模型 | 6 个 service 文件；`auth/service.py:12` | P2 |
| worker 侧 34 处 `print(f"...")` 无 traceback（错误路径丢栈） | worker/ 多文件 | P2 |

## 三、设计模式评估

**后端**：Route=闭包工厂+契约模型、Service=构造器注入、Repository=mixin 门面。模式运用一致且有自觉。两个结构性异议：

1. **JobQueries 门面是隐形式上帝接口**（P1）：23 个 mixin 线性组合出 ~200 个方法，接口隔离只是文档性的。建议按域拆子门面，service 只注入所需域。
2. **双 DI 风格并存**（P2）：一半 `RouterDeps` 显式注入、一半 `app.state` 服务定位器（20 个属性），新代码倾向挑松的一条。

**前端**：双轨状态管理（RQ=服务端态 / zustand=同步协议+UI 态）边界有头注释论证；容器/展示分离彻底（页面纯编排 9 个 hook）；Studio 巨型 hook 按 state/view 拆双 Context。SSE snapshot+patch+revision 协议（单调 revision 守卫、pending 队列溢出 resync、generation 计数器防并发快照互踩）是分布式前端状态同步的难点级正确实现。

**Worker/Velites**：监督退避区分可重试故障与配置错误；孤儿回收 killpg 前验证 argv 身份标记防 pgid 复用误杀；上传「崩溃至多丢结果、绝不重复上报」不变量完整；velites 仅 3 处 unsafe（全部信号处理必要场景，带 SAFETY 论证）。

## 四、测试覆盖与质量

**结论：测试是「一等工程系统」，覆盖深、质量高、flake 治理成熟。**

- **规模**：3714 个 Python 测试函数（10,109 断言，2.7/测试）+ 1477 前端用例 + 158 Rust 测试；测试:源码比 ~1.7:1。
- **CI 实测覆盖率**（合并分片后）：后端 94%（地板 85%）/ 前端 lines 87.4%、branches 79%（地板 72%）。
- **分层**：smoke（7 文件 <90s pre-push）→ unit（不可达 DB URL 证明纯层离线可跑）→ postgres（151 文件三哈希分片）→ full_gate（并发竞争证据层）→ nightly（三浏览器 e2e + 50 agent/2000 job 压测 + flake 门禁）。
- **flake 治理闭环**：注册表（6 条带根因+CI 链接+deadline）→ 遥测插件 → nightly 未注册 rerun 即红 → 注册表自身有 schema 测试。业内少见的成熟度。
- **反 mock 哲学**：全仓 `mock.patch` 仅 5 处；studio_chat 测试跑真实子进程做协议级断言（含「token 不出现在任何 SSE 载荷」的安全断言）。

**缺口（按严重度）**：

| 问题 | 严重度 |
|---|---|
| 85% 覆盖地板只量 `server/`——worker/（5.4K 行执行平面）、scripts/、shared/、workspace_libs/ 无覆盖门禁（aff-index 已证明多目录测量可行，只是没接到门禁） | **高** |
| agent_broker claim 链路核心模块（claim_scan/remote_artifacts/result_spool/manifest_guard）无直接单测面，仅集成间接覆盖——并发敏感路径定位成本高 | 中 |
| freezegun 声明依赖但全仓 0 使用；55 处直接 sleep（总 10.6s）靠真实时钟等待 | 中 |
| `repository_gate` 是死标记（全仓无正面应用） | 低 |
| 残余 flakiness：前端两次全量运行第一次 2 用例失败重跑全过（jsdom CPU 争抢家族，FLAKY-002 已知） | 低 |

## 五、性能

**后端**：同步路由由线程池承接、async 路由纪律性 `run_in_threadpool` 下放阻塞操作（多处注释「must not run on the loop alongside SSE/WS heartbeats」）；调度器 0.2s/3s 混合轮询+写路径即时唤醒+增量水位扫描+慢 pass 告警；SSE 有界队列+慢消费者驱逐。上次 review 的 P0（jobs.run_id 索引、gzip level 9、4 处 async auth、material TTL 事务内 S3、connection_tokens 行锁内 HTTP）已全部落地修复，并有回归测试。

**遗留**：

| 问题 | 位置 | 严重度 |
|---|---|---|
| 事件总线/限速器/聊天运行时纯进程内——多副本横向扩缩容时事件面静默失效，无代码或文档护栏 | `events/bus.py:27` 等 | **P1（约束）** |
| 遗留端点 `GET /workspaces/{id}/jobs` 无 LIMIT 全量拉取（前端已走 snapshot 分页端点，此端点仅剩 API 兼容） | `jobs/queries/job_nodes.py:116` | P1 |
| 无请求 ID / HTTP 耗时指标 / APM（OTel/Sentry/Prometheus 全库为零）——排障慢请求全靠猜 | 全局 | P1 |
| facets 一次请求 5 次串行池 checkout；`_notify_worker_poll` 每次 claim 附带 workspace 全表查 | `job_filtering.py:105-152`、`broker.py:144-169` | P2 |
| 高频路径重复解析 workflow snapshot JSON（无缓存） | `job_nodes.py:269` | P2 |

**前端**：首屏 JS ~738KB（gzip ~240KB），manualChunks 已把 xyflow/katex/marked/uplot/dompurify 隔离为路由级懒加载；虚拟列表每行独立 store 订阅（SSE patch 只重渲染变化行）；有 Playwright 压测断言 p95 延迟/long task/内存的 CI 防线。上次 review 的 P0 前端项（App.tsx selector、行组件 memo 链、JobDetail actions 签名门控）已全部落地。

**遗留**：

| 问题 | 位置 | 严重度 |
|---|---|---|
| DAG hover 全量重建节点/边对象击穿 memo——数百节点大图 hover 全量重渲染 | `DagGraph.tsx:204-257` | P1（大图） |
| JobDetail 页无 SSE，5s 轮询与列表实时性割裂 | `useJobDetail.ts:30-31` | P1 |
| katex.min.css 入口 eager 加载（含字体声明 ~23KB 打进所有页面首屏，包括登录页） | `main.tsx:12` | P2 |
| 同一 SSE 事件二次 JSON.parse | `workspaceEventHandlers.ts:33,40` | P2 |

## 六、安全

**结论：五个关键面（认证/CSRF/密钥/路径/SSRF）无 P0，实现普遍带威胁模型注释。**

- 密码 PBKDF2-SHA256 600k 迭代+常时比较+未知用户也付完整哈希成本（防时序枚举）；会话仅存 sha256、滑动过期、改密全 revoke。
- 路径穿越防御纵深（symlink 跟随+`is_relative_to` 收敛+CAS hash 白名单+bundle 名白名单）；SSRF 守卫含八进制/十六进制 IP 记法特判，DNS rebinding 局限显式文档化；SQL 注入面干净（f-string 只拼白名单标识符+静态占位符棘轮+运行时双护栏）。
- Vault（Fernet）密文落库+API 只出元数据+日志 redaction；沙箱密钥链：stdin 传递不落盘→env 白名单→结果 JSON 严格校验→错误信息脱敏 presigned URL。

**待改进**：

| 问题 | 位置 | 严重度 |
|---|---|---|
| **Worker 容器特权组合**：root + CAP_SYS_ADMIN + seccomp:unconfined + setuid bwrap——bwrap 一旦被绕过即宿主机 root（是 bwrap 在 Docker 默认 seccomp 下的已知必需项，注释有诚实记录，但属单点依赖） | `deploy/compose.worker.yaml`、`Dockerfile` | **P1** |
| 全部镜像以 root 运行（无 USER 指令） | `Dockerfile` | P1 |
| Vault 单 master key 无版本/轮换机制 | `services/vault.py` | P2 |
| Cookie `secure` 缺省为代码注释级约定（假设 TLS 终止于反代） | `routes/auth.py:39` | P2 |
| materials filename 无字符集校验即入 S3 key（可污染命名空间，无穿越语义） | `services/materials.py:130` | P2 |
| CSRF 自定义头存在性校验弱于随机 token（有 samesite=strict 双层补偿） | `auth/dependencies.py:44` | P2（可接受） |

## 七、与上次 review（2026-08-26）的对账

上次列出的「建议落地顺序」前 7 项**已全部落地**（索引/gzip/async auth/TTL 事务/单飞刷新/selector 化/memo 链），且中途的预算违规被 codex review 抓回并改为拆分文件——治理闭环真实运转。上次遗留未处理的项当前状态：

- Studio context 拆分 → **已做**（双 Context + state/view 拆分，本次核验）。
- token usage 6 查询 → **已修**（合并为 2 条聚合查询）。
- `sweep_expired_claims` 批量化、`job_deletion` 事务内 shutil.move、路由层 103 处 try/except 样板收敛、`agent_service._published_cache` 无上限 → **仍是遗留**（87 处样板、缓存无锁无上限均确认仍在）。
- 上次疑似 dead code（SubtitlePanel/TimelineStrip/VideoPlayer）→ **已删除**。

**新发现（本次独有）**：`architecture-exemptions.yaml` 的 8 个 file_budget 豁免中 **7 个挂钩的 GitHub issue 已 CLOSED 但豁免仍活跃**（#160/#188/#190/#195/#196/#198 均关闭，仅 #11 open）。豁免语义是「remove_when: issue 关闭」，即这些超预算文件（workflow_revisions.py 实际 199 行 vs 基线 95）按自身治理规则应已进入拆分/回落流程，但没有任何机制跟踪 issue 关闭事件——治理体系缺一个「豁免到期检测」的自动闭环。

## 八、优先行动建议（性价比排序）

1. **豁免到期清理**（半天）：7 个挂钩已关闭 issue 的 file_budget 豁免逐一处置——拆分回落或重挂有效 remove_when；顺手加「issue 关闭→豁免到期」的 CI 检测。
2. **前端 ErrorBoundary**（半天）：App 与 WorkspaceLayout 两层，chunk 加载失败触发 reload——当前白屏是最大的用户体验单点。
3. **`/workspaces/{id}/jobs` 加 LIMIT 或 410**（1 小时）：前端已走 snapshot 端点，此端点只欠一个防护。
4. **请求 ID + HTTP 耗时中间件**（1 天）：补上唯一的可观测性空白，排障能力立涨。
5. **Worker 容器特权收敛**（1-2 天）：至少加 `no-new-privileges`；评估 userns-remap；host/worker 镜像加非 root USER。
6. **worker/ 覆盖率地板**（1 天）：先出分区报告，稳定后转阻塞——补上执行平面的量化门禁。
7. **DAG hover 高亮下沉 node.data**（1 天）：大图场景 memo 失效修复。
8. **单副本护栏**（半天）：部署文档写明约束 + 启动时多副本探测告警。

## 九、总评

| 维度 | 评分（5 分制） | 一句话 |
|---|---|---|
| 代码质量 | 4.5 | 静态全绿、1 处 TODO、dict-as-DTO 是唯一系统性瑕疵 |
| 架构 | 4.0 | 分层干净、治理自动化罕见；JobQueries 门面与单进程假设是两个张力点 |
| 测试覆盖 | 4.5 | 94%/87% 实测、五层分级、flake 治理闭环；worker 无地板是唯一缺口 |
| 测试质量 | 4.5 | 2.7 断言/测试、反 mock 哲学、真实子进程协议级断言 |
| 设计模式 | 4.0 | 前后端模式一致且自觉；双 DI、双异常映射待收敛 |
| 性能 | 4.0 | 上次 P0 全清、有压测 CI 防线；遗留端点与 DAG hover 待修 |
| 安全 | 4.0 | 无 P0、威胁模型注释密度高；容器特权是单点 |
| 工程文化 | 5.0 | 预算棘轮+不变量注册+事故注释+退役台账，可审计性极佳 |

这个项目最突出的不是任何一个单项，而是**自我治理能力**：体积预算防失控、不变量注册表防回归、事故注释防重蹈、flake 注册表防麻木、退役台账防堆积。当前债务集中在「测量边界」（worker 覆盖、HTTP 观测、豁免到期跟踪）与「两个架构单点」（容器特权、单进程事件面），均为可控、可计划的结构性收尾工作，不存在系统性质量风险。
