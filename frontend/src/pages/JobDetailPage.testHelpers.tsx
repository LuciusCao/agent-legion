import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import JobDetailPage from './JobDetailPage'
import { useUiStore } from '../stores/uiStore'

export const mockDetail = {
  job: {
    id: 'j1',
    workspace_id: 'ws1',
    workflow_key: 'question_content',
    source_id: 'Q100',
    source_type: 'knowledge',
    title: 'Algebra Problem',
    status: 'running',
    created_at: '2026-06-09T07:59:00Z',
    updated_at: '2026-06-09T08:00:00Z',
  },
  nodes: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      label: '提取',
      status: 'completed',
      capability: 'extract',
      executor_id: 'code-default',
      executor_kind: 'code',
      after: [],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      error_message: '',
    },
    {
      id: 2,
      job_id: 'j1',
      node_key: 'generate',
      label: '生成',
      status: 'running',
      capability: 'generate',
      executor_id: 'pi',
      executor_kind: 'pi',
      after: ['extract'],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:13Z',
      error_message: '',
    },
    {
      id: 3,
      job_id: 'j1',
      node_key: 'review',
      label: '审核',
      status: 'stale',
      capability: 'review',
      executor_id: 'openclaw-default',
      executor_kind: 'openclaw',
      after: ['generate'],
      inputs: [],
      outputs: [],
      error_message: '',
    },
  ],
  runs: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      status: 'completed',
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      command_json: '[]',
      exit_code: 0,
      log_path: '',
      error_message: '',
    },
  ],
  artifacts: ['question.json'],
}

// JobDetailPage injects app-bar actions into useUiStore, but WorkspaceLayout/AppBar
// is not rendered in this isolated test, so ActionRenderer renders the stored actions
// so tests can interact with them.
function ActionRenderer() {
  const actions = useUiStore((state) => state.detailPageActions)
  return <div data-testid="detail-actions-host">{actions}</div>
}

export function renderPage(initialEntry = '/workspaces/ws1/jobs/j1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ActionRenderer />
      <Routes>
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<JobDetailPage />}
        />
        <Route
          path="/workspaces/:workspaceId"
          element={<div data-testid="workspace-main-page">Workspace Main</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

/** 只数 /api/jobs/j1 的 detail GET（产物 fetch 等其他请求不计入轮询断言）。 */
export function detailGetCalls(
  fetchMock: ReturnType<typeof createFetchMock>
): number {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === '/api/jobs/j1' && (init?.method ?? 'GET') === 'GET'
  ).length
}

export function createFetchMock(
  overrides: {
    detailStatus?: string
    packageUrl?: string | null
    pauseReason?: string | null
  } = {}
) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/jobs/j1' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...mockDetail,
          job: {
            ...mockDetail.job,
            status: overrides.detailStatus ?? 'running',
            execution_control:
              overrides.pauseReason != null
                ? {
                    paused: true,
                    pause_reason: overrides.pauseReason,
                    target_node_key: 'review',
                    mode: 'until_node',
                  }
                : undefined,
          },
        }),
      })
    }
    if (url === '/api/jobs/j1' && method === 'DELETE') {
      return Promise.resolve({
        ok: true,
        json: async () => ({ deleted: 'j1' }),
      })
    }
    if (
      url.startsWith('/api/jobs/j1/nodes/') &&
      url.endsWith('/rerun') &&
      method === 'POST'
    ) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'rerun',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/jobs/j1/run-to' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'run_to',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/jobs/j1/continue' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'continue',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/workspaces/ws1/jobs/package' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          download_url:
            overrides.packageUrl ?? '/api/workspaces/ws1/packages/pkg.zip',
          package_filename: 'pkg.zip',
          succeeded_count: 1,
          failed_count: 0,
          results: [{ job_id: 'j1', status: 'succeeded' }],
        }),
      })
    }
    if (url === '/api/jobs/j1/runs/1/token-usage' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          run_id: 1,
          usage: null,
          reason: 'no token usage recorded for run',
        }),
      })
    }
    if (url === '/api/jobs/j1/token-usage' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          currency: 'CNY',
          runs: [],
          total: {
            message_count: 0,
            input_tokens: 0,
            output_tokens: 0,
            cache_read_tokens: 0,
            total_tokens: 0,
            cost: {
              input: 0,
              output: 0,
              cache_read: 0,
              total: 0,
              currency: 'CNY',
            },
            pricing_missing: false,
          },
          runs_with_usage: 0,
          runs_without_usage: 0,
        }),
      })
    }
    if (url === '/api/jobs/j1/artifacts/question.json' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({ name: 'question.json', content: '{}' }),
      })
    }
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}
