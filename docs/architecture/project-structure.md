# 项目结构

本文件列出 Agent Legion 仓库的高层目录结构。详细的模块说明见 [backend.md](backend.md) 和 [frontend.md](frontend.md)。

> 具体文件清单以实际文件系统为准；本文件只记录稳定的顶层模块和关键入口。

```text
agent-legion/
├── pyproject.toml              # Python project metadata, dependencies, tool config
├── uv.lock                     # Locked Python dependency tree
├── README.md                   # 项目入口文档（中文）
├── README_EN.md                # 项目入口文档（English）
├── AGENTS.md                   # Agent 操作手册与开发红线
├── .env.example                # 运行时密钥与覆盖项模板
├── Makefile                    # 常用命令快捷方式
├── config/                     # 按领域拆分的配置
│   ├── agent-worker.example.yaml # Agent Worker 配置模板
│   └── architecture/           # 架构治理配置
│       ├── architecture-invariants.yaml
│       ├── architecture-exemptions.yaml
│       ├── architecture-budget-policy.yaml
│       ├── architecture-budgets.json
│       ├── test-root-files-baseline.json
│       └── sql-placeholders-baseline.json
│   # 运行时 split 配置（app.yaml / workflow.yaml / agent_legion.yaml）已退役：
│   # 代码默认值 + env 覆盖 + DB 实例设置文档，文件存在即启动报错。
│   # skill 源与锁（skills.yaml / skills.lock）亦已退役：存 DB global_settings
│   # （skill_sources / skill_lock），经 admin API 与 make skills-lock 管理；
│   # 残留文件启动时一次性导入 DB（warning），此后不再读取。
├── server/
│   └── app/
│       ├── main.py             # FastAPI app factory + lifespan worker
│       ├── settings.py         # 配置加载与合并
│       ├── routes/             # REST API 路由与合约
│       ├── services/           # 业务逻辑服务层
│       ├── db/                 # PostgreSQL schema、连接池、事务与共享查询构造
│       ├── jobs/               # Job 领域查询与类型
│       ├── executors/          # Code executor、lease 调度与 capacity 控制
│       ├── workflows/          # Agent Legion DAG 定义与执行
│       ├── configuration/      # 配置加载与 owned-keys 校验
│       ├── quality/            # 架构不变量与豁免运行时检查
│       ├── skills/             # 外部 skill 源与锁管理
│       ├── events/             # 事件子系统：sse.py SSE 广播、bus.py 进程内总线、
│       │                       # buffer.py DB 持久化缓冲、aggregator.py 聚合器、
│       │                       # agents.py Agent 发现与状态跟踪
│       ├── workflow_worker/    # DAG workflow worker：thread.py 线程与 poll 循环、
│       │                       # ready.py 每 pass 一次的 ready 候选收集、
│       │                       # schedule.py ready 候选的 lease 认领与提交
│       └── worker*.py          # Worker 控制与启动（worker_control.py / worker_startup.py）
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx            # React entry point
│       ├── App.tsx             # 应用级 Provider
│       ├── AppRoutes.tsx       # 路由定义
│       ├── api/                # 按领域拆分的 API 层（index.ts barrel 统一导出）
│       ├── generated/api.ts    # OpenAPI 生成的传输类型
│       ├── pages/              # 路由级页面
│       ├── layouts/            # 布局组件
│       ├── components/         # 可复用 UI 组件
│       ├── stores/             # Zustand 客户端状态管理
│       ├── hooks/              # React 自定义 Hooks
│       ├── lib/                # 纯工具函数
│       ├── types/              # 类型声明
│       ├── testing/            # 测试辅助
│       └── styles.css          # 全局样式
├── worker/                     # Agent Worker 协议 v2 实现
│   ├── service.py              # Worker Service 控制面入口
│   ├── executor.py             # claim / 执行 / 心跳 / 结果上报主循环
│   ├── runtime_controls.py     # 状态副本 YAML 热更控制
│   └── ...
├── velites/                    # 自研 Rust agent harness 与 OS 沙箱
│   ├── src/
│   ├── tests/
│   └── Cargo.toml
├── shared/                     # Host 与 Worker/节点 SDK 共享的轻量契约
│   ├── pi_events.py
│   ├── pi_model_error.py
│   ├── material_cache.py       # 材料物化缓存（内容寻址、原子写入、按字节预算淘汰）
│   └── material_bundle.py      # bundle 文件夹条目的清单与确定性地址硬链接物化
├── workspace_libs/             # 节点 SDK 与执行脚手架（code 节点沙箱/Worker 闭包白名单）
├── scripts/                    # 质量门、迁移、生成器
│   ├── check-quick.sh          # 快速质量门
│   ├── check.sh                # 完整质量门
│   ├── check-ci.sh             # CI 质量门
│   ├── check_architecture.py   # 架构契约检查
│   ├── check_invariants.py     # 不变量/豁免校验
│   ├── check-skills-shared.py  # Skill 共享资源一致性检查
│   ├── verify_specs.py         # Spec 健康检查
│   ├── ratchet_architecture_budgets.py # 架构预算基线更新
│   ├── generate_architecture.py # 自动生成架构文档表格
│   ├── generate-api-types.sh   # 生成前端 API 类型
│   └── install-git-hooks.sh    # 预提交钩子安装
├── tests/                      # pytest 测试套件
│   ├── conftest.py
│   ├── test_*.py               # 单元/集成测试（存量；新测试禁止放根目录——
│   │                           # `scripts/architecture/test_placement.py` +
│   │                           # `config/architecture/test-root-files-baseline.json`
│   │                           # 强制，须进对应子系统子目录）
│   ├── test_architecture_*.py  # 架构契约测试
│   ├── routes/                 # 路由级测试
│   ├── full/                   # 高保真完整门测试
│   └── ci/                     # CI 扩展压力测试
├── deploy/                     # Docker Compose 与部署模板
│   ├── compose.host.yaml
│   ├── compose.worker.yaml
│   └── secrets/
├── examples/                   # 示例 workflow 资源
│   └── education-video-problems-generation/
├── docs/
│   └── architecture/           # 架构文档
└── data/                       # 运行时数据（gitignored）
    ├── videos/
    ├── jobs/
    ├── packages/
    └── logs/
```

## 关键约定

- `config/architecture/` 下的预算是机器维护的（`architecture-budgets.json`）或人工维护的策略（`architecture-budget-policy.yaml`），通过 `scripts/check_architecture.py` 和 `scripts/check_invariants.py` 在质量门中执行。
- `frontend/src/generated/api.ts` 由后端 OpenAPI 模式生成，禁止手写重复传输类型。
- `data/` 目录已加入 `.gitignore`，禁止提交运行时数据或密钥。
- 多 worktree 开发时，每个 worktree 应使用独立的后端端口和 `data/` 目录，避免状态互相覆盖。
