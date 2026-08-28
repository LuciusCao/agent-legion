# 前端架构

## Overview

Agent Legion 前端是 React 18 + TypeScript SPA，使用 Vite 构建。UI 基于 `@mui/material` (MUI v6) + CSS Modules + 自定义 CSS。

核心职责：

- Workspace 列表与详情展示
- Job 列表、Job Detail（含 DAG、产物、日志、视频播放器）
- Workflow Studio（工作流可视化编辑）
- Token Usage 用量统计
- Settings（Workspace / 全局设置 / Skill 源 / 外部服务连接等）
- 通过 SSE 接收后端事件，通过 WebSocket 接收 Agent 状态

## Directory Structure

```
frontend/src/
├── main.tsx                # React 入口
├── App.tsx                 # 应用壳（渲染 AppRoutes + 全局 Toast，建立 agents WebSocket；ThemeProvider 在 main.tsx）
├── AppRoutes.tsx           # React Router v6 路由定义
├── api/                    # 按领域拆分的 API 层
│   ├── index.ts            # barrel：纯 re-export 各领域模块
│   ├── core.ts             # 通用请求封装
│   ├── workspaceApi.ts / jobsApi.ts / jobApi.ts / jobBatchApi.ts / jobSnapshot.ts
│   ├── workflows.ts / workflowRevisions.ts / workflowDraftCompare.ts
│   └── ...                 # agentCatalogApi、packages、tokenUsage 等
├── generated/
│   └── api.ts              # OpenAPI 生成的传输类型
├── pages/                  # 路由级页面
│   ├── LoginPage.tsx
│   ├── SetupPage.tsx
│   ├── DashboardPage.tsx
│   ├── MonitoringPage.tsx
│   ├── WorkspaceMainPage.tsx
│   ├── JobDetailPage.tsx
│   ├── SettingsPage.tsx
│   ├── GlobalSettingsPage.tsx
│   ├── UsersAdminPage.tsx
│   ├── QualityPage.tsx
│   ├── TokenUsagePage.tsx
│   ├── WorkflowStudioPage.tsx
│   ├── jobDetail/          # Job Detail 子组件
│   └── workflowStudio/     # Workflow Studio 子组件与 hooks（含 chat/ Studio chat 面板）
├── layouts/                # 布局组件
│   ├── AppShell.tsx
│   └── WorkspaceLayout.tsx
├── components/             # 可复用 UI 组件（按领域子目录组织）
│   ├── job/                # Job 列表/操作组件（JobList、JobActionBar、JobProgressPanel 等）
│   ├── dag/                # DAG 图组件（DagGraph、DagNode、DagStepper 等）
│   ├── artifact/           # 产物组件（ArtifactListDialog、ArtifactPopover、ArtifactPreviewDialog）
│   ├── tokenUsage/         # Token 用量组件（TokenUsagePanel、TokenUsageDialog 等）
│   ├── question/           # 审题内容组件（QuestionContentPanel、QuestionAnnotations 等）
│   ├── JobRerunDialog/     # Job 重跑对话框（已有子目录）
│   ├── settings/           # Settings 区块组件（已有子目录）
│   ├── AddItemsDialog.tsx      # runs 添加条目对话框
│   ├── AddItemsUploadPanel.tsx # 条目类型面板：material 上传
│   ├── AddItemsRefPanel.tsx    # 条目类型面板：ref 外部引用
│   ├── AddItemsBundlePanel.tsx # 条目类型面板：bundle 文件夹
│   ├── AddDialog.tsx
│   ├── VideoContentPanel.tsx     # Job Detail 视频内容面板
│   ├── RichText.tsx              # CMS 富文本（HTML + LaTeX）统一渲染
│   └── ...
├── stores/                 # Zustand 客户端状态管理
│   ├── jobStore.ts         # job/ 家族唯一对外入口（shim re-export）
│   ├── job/                # Job 领域子状态
│   ├── setting/
│   └── ...
├── hooks/                  # React 自定义 Hooks
│   ├── useWorkspaceEvents.ts
│   ├── useJobComprehensionInfo.ts
│   └── ...
├── lib/                    # 纯工具函数
│   ├── jobDag.ts
│   ├── jobRuns.ts
│   ├── workflowNodes.ts
│   └── ...
├── types/                  # 类型声明
├── testing/                # 测试辅助（mock、fixture、TestMemoryRouter）
└── styles.css              # 全局样式
```

## Data Flow

```
用户交互 → React Query / Zustand Store → api/ 模块 → FastAPI 后端
              ↓
        组件重新渲染 ← SSE / WebSocket 事件推送
```

- 服务端状态（workspace、job、workflow 等）优先通过 **React Query**（`@tanstack/react-query`）获取与缓存，Zustand 保留客户端 UI 状态。
- Workspace / Job 状态变更通过 SSE 推送。
- Agent 状态通过 WebSocket (`/api/agents`) 推送。
- 前端 `api/` 层负责按领域组织请求，经 `api/index.ts` barrel 统一导出。

## Key Decisions

- 使用 Zustand 管理客户端 UI 状态，服务端状态通过 React Query 获取、缓存与失效，降低样板代码。
- 状态按领域拆分（`jobStore`、`job/*`、`setting/*` 等），避免单文件过大。
- 使用 MUI v6 组件库 + CSS Modules 管理局部样式。
- 路由定义集中在 `AppRoutes.tsx`；`App.tsx` 只负责渲染 `AppRoutes`、全局 Toast 与 agents WebSocket 连接，应用级 Provider（如 ThemeProvider）在 `main.tsx`。
- 前端传输类型必须从 `frontend/src/generated/api.ts` 派生，禁止手写重复类型。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 页面路由

| 路径 | 页面组件 |
|------|----------|
| `/login` | LoginPage |
| `/setup` | SetupPage |
| `/admin/*` | AdminRoutes |
| `/` | DashboardPage |
| `/monitoring` | MonitoringPage |
| `/workspaces/:workspaceId` | WorkspaceLayout |
| `/workspaces/:workspaceId` | WorkspaceMainPage |
| `/workspaces/:workspaceId/jobs/:jobId` | JobDetailPage |
| `/workspaces/:workspaceId` | SettingsPage |
| `/workspaces/:workspaceId/token-usage` | TokenUsagePage |
| `/workspaces/:workspaceId/monitoring` | MonitoringPage |
| `/workspaces/:workspaceId/quality` | QualityPage |
| `/workspaces/:workspaceId/workflow-studio` | WorkflowStudioPage |

<!-- END AUTO-GENERATED -->

## Technology Stack

- React 18
- TypeScript 5.8
- Vite
- React Router v6
- React Query (`@tanstack/react-query`)
- Zustand
- `@mui/material` (MUI v6)
- `@xyflow/react` (React Flow)
- `dagre`
- `katex`
- `@tanstack/react-virtual`

## Tooling

- **Linter**: ESLint 10 + `typescript-eslint` + `eslint-plugin-react-hooks`
- **Formatter**: Prettier（`semi: false`, `singleQuote: true`, `tabWidth: 2`）
- 常用脚本：
  - `npm run dev`
  - `npm run build`
  - `npm run typecheck`
  - `npm run preview`
  - `npm run api:generate`
  - `npm run api:check`
  - `npm run test`
  - `npm run test:coverage`
  - `npm run lint`
  - `npm run lint:fix`
  - `npm run format`
  - `npm run format:check`

## Testing Conventions

- 测试框架：Vitest + `@testing-library/react` + jsdom。
- 覆盖率阈值（`vite.config.ts`）：lines 86 / functions 80 / branches 72 / statements 82。
- 测试辅助位于 `frontend/src/testing/`：
  - `eventSourceMock.ts` — `EventSource` mock
  - `TestMemoryRouter.tsx` — 测试用路由包装器
  - `fixtures.ts` — 通用测试数据
- 全局测试设置：`frontend/src/test-setup.ts`，包含 console-error 断言辅助、`ResizeObserver` / `IntersectionObserver` mock 等。

## UI Behavior Notes

- 前端界面语言为中文。
- Workspace 列表展示 job 统计、最近活动和快速操作。
- Job Detail 包含 DAG 图、Stepper、产物（Artifact）面板、日志、视频播放器（针对视频 Job）。
- Workflow Studio 支持可视化编辑 workflow 节点、边与 intake modes，并与修订历史集成；Agent 节点按 capability 读取 Agent Catalog，显示 skill/tools 和全局运行默认值，并可编辑 provider/model/thinking/prompt 覆盖。画布区 DAG 是唯一常驻主视图：变更与校验结果在右侧 Drawer（顶栏状态 chip 点击、校验完成、发布前 review 打开），YAML 编辑降级为画布工具栏「编辑 YAML」按钮打开的全屏 Dialog（结构性编辑唯一入口）。顶栏状态收敛为单个 chip：无变更显示「已同步」、查看历史 revision 显示「只读 vN」、有变更显示「未发布变更 N」（颜色编码风险等级，明细在 tooltip，点击打开变更面板），compare 计算中与「已保留当前草稿」并入同一 chip。
- Token Usage 页面展示 workspace / job / run 级别的 token 用量与成本。
- 全局 `Toast` 组件用于操作反馈。
