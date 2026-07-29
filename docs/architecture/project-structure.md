# 项目结构

本文件列出 Agent Legion 仓库的高层目录结构。详细的模块说明见 [backend.md](backend.md)、[frontend.md](frontend.md) 和 [pipeline.md](pipeline.md)。

> 具体文件清单以实际文件系统为准；本文件只记录稳定的顶层模块和关键入口。

```text
video-hive/
├── pyproject.toml              # Python project metadata, dependencies, tool config
├── uv.lock                     # Locked Python dependency tree
├── README.md                   # 项目入口文档
├── AGENTS.md                   # Agent 操作手册与开发红线
├── .env.example                # 运行时密钥与覆盖项模板
├── Makefile                    # 常用命令快捷方式
├── config/                     # 按领域拆分的配置
│   ├── app.yaml                # 应用路径、PostgreSQL URL、HTTP 设置、清理、监控、token 定价
│   ├── agent_legion.yaml       # ASR、CMS、资源提供方、OpenClaw
│   ├── workflow.yaml           # Workspace 执行器、工作流运行时、Pi 配置
│   ├── skills.yaml             # 外部 Pi skill 源声明
│   ├── skills.lock             # 解析后的 skill commit 锁定
│   ├── architecture/           # 架构治理配置
│   │   ├── architecture-invariants.yaml
│   │   ├── architecture-exemptions.yaml
│   │   ├── architecture-budget-policy.yaml
│   │   └── architecture-budgets.json
│   └── workflows/              # Workflow DAG 定义
│       ├── video_knowledge.yaml
│       └── question_comprehension_info.yaml
├── server/
│   └── app/
│       ├── main.py             # FastAPI app factory + lifespan worker
│       ├── settings.py         # 配置加载与合并
│       ├── routes/             # REST API 路由与合约
│       ├── services/           # 业务逻辑服务层
│       ├── db/                 # PostgreSQL schema、连接池、事务与共享查询构造
│       ├── jobs/               # Job 领域查询与类型
│       ├── executors/          # Executor 配置、runtime、lease 调度
│       ├── workflows/          # Agent Legion DAG 定义与执行
│       ├── pipeline/           # 视频处理流水线阶段
│       ├── cms/                # CMS API 集成
│       ├── configuration/      # 配置加载与 owned-keys 校验
│       ├── quality/            # 架构不变量与豁免运行时检查
│       ├── video_capabilities/ # 视频能力合约与投影
│       ├── skills/             # 外部 Pi skill 管理
│       ├── agents.py           # Agent 发现与状态跟踪
│       ├── events.py           # SSE 事件广播
│       ├── workflow_worker_thread.py # DAG workflow worker 线程与 poll 循环
│       ├── workflow_worker_ready.py  # 每 pass 一次的 ready 候选收集（批量状态查询）
│       ├── workflow_worker_schedule.py # ready 候选的 lease 认领与提交
│       └── worker*.py          # Worker 控制与遗留 worker 文件
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
│       ├── stores/             # Zustand 状态管理
│       ├── hooks/              # React 自定义 Hooks
│       ├── lib/                # 纯工具函数
│       ├── types/              # 类型声明
│       ├── testing/            # 测试辅助
│       └── styles.css          # 全局样式
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
│   ├── test_*.py               # 单元/集成测试
│   ├── test_architecture_*.py  # 架构契约测试
│   ├── routes/                 # 路由级测试
│   ├── full/                   # 高保真完整门测试
│   └── ci/                     # CI 扩展压力测试
├── tools/
│   └── content-uploader/ # 审题信息上传 CLI 工具
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
