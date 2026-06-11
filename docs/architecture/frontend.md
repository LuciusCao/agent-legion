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
| `jobs` | WorkspaceJobListWrapper |
| `jobs/:jobId` | JobDetailPage |
| `packages` | WorkspacePackagesPage |
| `/workspaces/:workspaceId/settings` | SettingsPage |
| `/videos/:id` | DetailPage |

<!-- END AUTO-GENERATED -->

## Related Specs

- [前端状态管理](../superpowers/completed/2026-05-29-frontend-state-management-design.md)
- [Material Design 3 前端改造](../superpowers/completed/2026-05-23-material-design-3-redesign-design.md)
