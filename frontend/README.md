# Agent Legion Frontend

React 18 + TypeScript + Vite SPA for the Agent Legion console.

## Technology Stack

- React 18
- TypeScript 5.8
- Vite
- React Router v6
- Zustand
- `@mui/material` (MUI v6)
- `@xyflow/react` (React Flow)
- `dagre`
- `katex`
- `@tanstack/react-virtual`

## Setup

```bash
cd frontend
npm install
```

Copy the dev proxy config:

```bash
cp .env.example .env
```

`.env` contains `VITE_API_TARGET`, which defaults to `http://127.0.0.1:8000`.

## Available Scripts

```bash
npm run dev              # Start Vite dev server (host 127.0.0.1)
npm run build            # Type-check and production build
npm run preview          # Preview the production build
npm run typecheck        # Run tsc --noEmit
npm run api:generate     # Regenerate frontend API types from OpenAPI
npm run api:check        # Check that generated types are up-to-date
npm run test             # Run Vitest once
npm run test:coverage    # Run Vitest with coverage
npm run lint             # Run ESLint
npm run lint:fix         # Run ESLint with auto-fix
npm run format           # Run Prettier write
npm run format:check     # Run Prettier check
```

## API Types

Frontend transport types are generated from the backend OpenAPI schema and committed to `src/generated/api.ts`.

```bash
npm run api:generate
```

After changing any Pydantic response model in the backend, regenerate and commit `src/generated/api.ts`. Do not hand-write duplicate transport types; derive from `src/generated/api.ts` instead.

## Project Conventions

- **Pages**: route-level components live in `src/pages/`.
- **Layouts**: `src/layouts/AppShell.tsx` and `src/layouts/WorkspaceLayout.tsx`.
- **API layer**: all API modules live in `src/api/` (`core.ts` is the low-level fetch wrapper; `index.ts` is the workspace/job client entry re-exporting the focused modules). Do not add API modules at the `src/` root.
- **Types**: shared types live in `src/types/` (`index.ts` is the barrel re-exporting `jobTypes.ts` etc.); derive transport types from `src/generated/api.ts`, do not hand-write duplicates. `JobSummary` is defined only in `src/types/jobTypes.ts`.
- **State**: Zustand stores in `src/stores/`. Two layouts exist: directory-style (`stores/job/`, `stores/setting/`, split into `state`/`actions`/`selectors`) and single-file (`workspaceStore.ts`, `uiStore.ts`, ...). Directory-style is the extension paradigm — use it for new domains or when a store grows actions/selectors; keep small UI-only stores single-file.
  Data that must go through a store: shared server state read by more than one component or updated by WS events (jobs, workspaces, agents, settings, executors). One-shot, single-consumer fetches local to a dialog/panel (e.g. job log, token usage, artifacts) may call `src/api/` functions directly from the component/hook.
- **Styles**: MUI v6 theme in `src/theme.ts`; component-level styles in CSS Modules (`*.module.css`); global styles in `src/styles.css`.
- **Helpers**: pure utility functions in `src/lib/`.
- **Hooks**: React custom hooks in `src/hooks/`.
- **Testing**: Vitest + `@testing-library/react` + jsdom. Helpers and mocks in `src/testing/`.

## Testing

Coverage thresholds (in `vite.config.ts`):

- lines: 86
- functions: 80
- branches: 72
- statements: 82

Run tests:

```bash
npm run test
npm run test:coverage
```

## Development Proxy

During development, Vite proxies `/api` to `VITE_API_TARGET`. To point at a backend running on a different port:

```bash
echo 'VITE_API_TARGET=http://127.0.0.1:8001' > .env
```
