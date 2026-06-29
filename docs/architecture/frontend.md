# 前端架构

## Overview

Video Hive 前端是 React 18 + TypeScript SPA，使用 Vite 构建。UI 基于 `@material/web` Material 3 Web Components + 自定义 CSS。

## Directory Structure

```
frontend/src/
├── main.tsx                # React 入口
├── App.tsx                 # 路由壳（React Router v6）
├── api.ts                  # 后端 API 封装
├── types.ts                # 共享类型定义
├── pages/                  # 路由级页面
│   ├── ListPage.tsx        # 视频列表
│   ├── DetailPage.tsx      # 视频详情
│   ├── JobDetailPage.tsx   # Job 详情
│   └── WorkspaceMainPage.tsx # Workspace 主页
├── components/             # 可复用 UI 组件
│   ├── VideoPlayer.tsx
│   ├── SubtitlePanel.tsx
│   ├── ChapterPanel.tsx
│   └── ...
├── stores/                 # Zustand 状态管理
│   ├── videoStore.ts
│   ├── detailStore.ts
│   └── ...
├── hooks/                  # React 自定义 Hooks
└── helpers.ts              # 纯工具函数
```

## Data Flow

```
用户交互 → Zustand Store → api.ts → FastAPI 后端
              ↓
         组件重新渲染 ← SSE 事件推送
```

前端通过 SSE 接收后端事件（视频状态变更、Agent 状态更新），实现准实时 UI 更新。

## Key Decisions

- 使用 Zustand 而非 Redux，降低样板代码。
- 状态按页面拆分（videoStore、detailStore、jobStore 等），避免单文件过大。
- `@material/web` 组件通过 React ref 和事件监听集成。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 页面路由

| 路径 | 页面组件 |
|------|----------|
| `/` | DashboardPage |
| `/video-hive` | VideoHiveLayout |
| `(index)` | ListPage |
| `/video-hive/settings` | VideoHiveSettingsPage |
| `/workspaces/:workspaceId` | WorkspaceLayout |
| `(index)` | WorkspaceMainPage |
| `jobs/:jobId` | JobDetailPage |
| `packages` | WorkspacePackagesPage |
| `/workspaces/:workspaceId/settings` | SettingsPage |
| `/videos/:id` | DetailPage |

<!-- END AUTO-GENERATED -->

## Related Specs

- [前端状态管理](../superpowers/completed/2026-05-29-frontend-state-management-design.md)
- [Material Design 3 前端改造](../superpowers/completed/2026-05-23-material-design-3-redesign-design.md)

## Technology Stack

- React 18
- TypeScript 5.8
- Vite
- React Router v6
- Zustand
- `@material/web` (Material 3 Web Components)
- `@mui/material` (MUI)
- `@xyflow/react` (React Flow)
- `dagre`
- `katex`
- `@tanstack/react-virtual`

## Tooling

- **Linter**: ESLint 10 + `typescript-eslint` + `eslint-plugin-react-hooks`
- **Formatter**: Prettier（`semi: false`, `singleQuote: true`, `tabWidth: 2`）
- 常用脚本：
  - `npm run lint`
  - `npm run lint:fix`
  - `npm run format`
  - `npm run format:check`

## UI Behavior Notes

- 前端界面语言为中文（如“加入队列”、“重跑”、“打包完成项”）。
- 视频播放器支持点击 subtitle / chapter / interaction 时间戳进行 seek。
- 添加视频表单包含类型选择（知识点 / 题目解析）、`external_id` 字段与可选 `source_uuid`。
- 批量输入支持每行 `external_id,source_uuid`（`source_uuid` 可选）。
- 列表与详情头部展示 `content_type` 标签与 `external_id`。
- Phase panel 与重跑下拉框会根据 `content_type` 自适应；`question` 视频隐藏 knowledge-only phases。
- 全局 `Toast` 组件用于反馈（如“该资源正在被处理中”）。
- 批量操作支持多选后批量重跑、批量删除、批量打包。
