# 项目结构

仓库目录地图：只列到有意义的层级，大包一行说明职责。文件级清单以实际文件系统为准；
模块内部机制见 [backend.md](backend.md)、[frontend.md](frontend.md)、
[velites-harness.md](velites-harness.md)，脚本清单见 [scripts/README.md](../../scripts/README.md)，
运行时 `data/` 布局见 [data-layout.md](../data-layout.md)。

```text
agent-legion/
├── README.md / README_EN.md    # 项目入口（中 / 英，双向同步）
├── AGENTS.md                   # Agent 操作手册与开发红线
├── CONTRIBUTING.md             # 贡献流程：setup、首次跑测试、PR 约定
├── CHANGELOG.md
├── Makefile                    # 常用命令入口（make help 列全集）
├── pyproject.toml / uv.lock    # Python 依赖与工具配置
├── .python-version             # uv 管理的 Python 钉点
├── .env.example                # env-only 配置模板（DB URL、S3、vault key 等）
├── Dockerfile                  # host / worker 多阶段镜像
├── .githooks/                  # 版本化 pre-commit / pre-push（make install-hooks 安装）
├── .github/                    # CI workflow（quality-gate / nightly-gate / 镜像与 velites 发布）、
│                               # issue / PR 模板、dependabot
├── config/
│   └── architecture/           # 架构治理注册表：invariants / exemptions / 体积预算与策略、
│                               # 执行写面、测试放置与 smoke 清单、postgres 测试清单、
│                               # 服务数据边界与 SQL 占位基线、退役术语表、issue 状态清单
│   # 运行时 split yaml 已全部退役：配置 = 代码默认值 + env 覆盖 + DB 实例设置文档
├── server/app/                 # FastAPI Host（控制面）
│   ├── main.py                 # app factory + lifespan
│   ├── bootstrap/              # composition root：按领域组装应用对象图
│   ├── routes/                 # REST 路由与 contract（薄 HTTP 适配层）
│   ├── services/               # 业务逻辑；子包 job_rerun/ ops_metrics/
│   │                           # failure_classification/ runtime_profile/
│   ├── jobs/                   # Job 领域类型与 JobQueries 门面（queries/）
│   ├── db/                     # PostgreSQL schema、迁移链（migrations/）、连接池与事务
│   ├── workflows/              # DAG 定义、loader、节点类型与发布校验
│   ├── workflow_worker/        # DAG 调度线程：ready 收集、lease 认领、dispatch 配置注入
│   ├── executors/              # 容量 lease、隐含 code 池与本地 code 执行
│   ├── agent_broker/           # agent 执行队列：claim / sweep / dispatch
│   ├── agent_control/          # Worker 注册、scoped register token、声明与在线管理
│   ├── agent_catalog/          # Agent 定义模型（#440 过渡期）与 demo 模板
│   ├── agent_runtime/          # runtime catalog（AGENT_RUNTIMES）与 adapter
│   ├── auth/                   # 用户 / 会话 / 限流 / workspace 访问依赖注入
│   ├── configuration/          # 配置合成与 owned-key / 退役文件校验
│   ├── events/                 # SSE 广播、进程内总线、事件缓冲与聚合
│   ├── skills/                 # skill root、执行副本导出与 skill_lock
│   ├── storage/                # 实例对象存储（S3 设置与客户端）
│   ├── studio_chat/            # Studio 对话：ACP agent 子进程、会话、后台接续
│   ├── mcp_server/             # Studio agent 的 MCP 工具面（stdio / HTTP）
│   └── worker_control*.py 等   # workspace 暂停/恢复控制、启动任务、HTTP 中间件
├── worker/                     # Agent Worker（协议版本见 shared/protocol.py）
│   ├── service.py / cli.py     # Worker Service 控制面与命令行入口
│   ├── executor.py             # claim → 执行 → 结果上报主循环
│   ├── execution/              # 单次执行：准备 / 运行 / 生命周期 / 心跳
│   ├── runtime/                # 声明解析、热更控制、模型发现、启动预检
│   ├── host/                   # Host 控制面 HTTP 客户端与状态同步
│   ├── registration/           # scoped token 与注册重试
│   ├── artifact/ upload/       # presigned 传输原语与产物直传队列
│   ├── status/                 # 执行状态文件
│   └── ui/                     # Worker 控制台静态前端（node:test 覆盖）
├── shared/                     # Host 与 Worker / 节点 SDK 共享的轻量契约（协议常量、
│                               # 沙箱 env、材料缓存与 bundle 物化、脱敏等）
├── workspace_libs/             # 节点 SDK（NodeContext）与 code 节点执行脚手架
├── workflow_nodes/             # demo workflow 的内置 code 节点
├── velites/                    # 自研 Rust agent harness 与 OS 沙箱
│   ├── src/                    # agent 循环、provider/、tools/、sandbox/、事件
│   ├── schema/events.schema.json
│   └── tests/
├── frontend/                   # React SPA（Vite）
│   ├── src/
│   │   ├── main.tsx / App.tsx / AppRoutes.tsx
│   │   ├── routes/             # 路由表拆分（admin 路由、页面懒加载表）
│   │   ├── pages/              # 路由级页面
│   │   ├── features/           # 自成体系的功能域：workflowStudio / agentPanelDock /
│   │   │                       # jobDiagnosis / previewPanel
│   │   ├── components/ layouts/ hooks/ stores/ lib/ types/ testing/
│   │   ├── api/                # 按领域拆分的 API 层
│   │   └── generated/api.ts    # OpenAPI 生成的传输类型（禁止手写）
│   ├── e2e/                    # Playwright 浏览器 smoke
│   ├── stress/                 # workspace 压测 spec（nightly-e2e）
│   └── scripts/                # 覆盖率清点等辅助脚本
├── scripts/                    # 质量门、架构治理（architecture/、quality/）、dev/prod 启停、
│                               # 迁移与种子、E2E / 压测、LLM 网关（remote/）
├── tests/                      # pytest；新测试按子系统放子目录（routes/ services/ db/
│                               # workers/ scripts/ workflows/ …），根目录只留 conftest
│                               # 与共享支撑（test_placement.py 强制）；full/ 为高保真
│                               # 证据层，ci/ 为 nightly 扩展压测
├── deploy/                     # Docker Compose（host / worker 各形态）与配置模板
│   └── secrets/                # 本机密钥（gitignored，install / init-worktree 生成）
├── examples/                   # demo workflow 资源与 demo skills
├── docs/                       # 文档（层级见 docs/README.md）
└── data/                       # 运行时数据（gitignored，布局见 docs/data-layout.md）
```

## 关键约定

- `config/architecture/` 下的预算与基线由机器维护（ratchet 脚本），策略与注册表人工维护，经
  `scripts/check_architecture.py` 与 `scripts/check_invariants.py` 在质量门执行。
- `frontend/src/generated/api.ts` 由后端 OpenAPI 生成，禁止手写传输类型。
- `data/` 与 `deploy/secrets/` 已 gitignore，禁止提交运行时数据或密钥。
- 多 worktree 开发时每个 worktree 使用独立端口、`data/` 目录与派生数据库（见 AGENTS.md §1）。
