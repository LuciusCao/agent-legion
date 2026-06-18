import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobDetailPage from './JobDetailPage'
import { useUiStore } from '../stores/uiStore'

const mockDetail = {
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
      executor_id: 'local-default',
      executor_kind: 'local',
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
      executor_id: 'pi-default',
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

function renderPage(initialEntry = '/workspaces/ws1/jobs/j1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ActionRenderer />
      <Routes>
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<JobDetailPage />}
        />
        <Route
          path="/workspaces/:workspaceId/jobs"
          element={<div data-testid="job-list-page">Job List</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

function createFetchMock(
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
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  it('renders page with job detail', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    renderPage()

    expect(await screen.findByText('提取')).toBeInTheDocument()
    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成').length).toBeGreaterThanOrEqual(1)
  })

  it('opens fullscreen DAG dialog from progress panel', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    const { container } = renderPage()
    await waitFor(() => {
      expect(screen.getByLabelText('查看 DAG')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByLabelText('查看 DAG'))
    expect(await screen.findByLabelText('关闭')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-testid="dag-node"]')).toHaveLength(
      3
    )
    expect(
      container.querySelector('[data-testid="dag-node"][data-status="stale"]')
    ).toBeInTheDocument()
    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('审核').length).toBeGreaterThanOrEqual(1)
  })

  it('polls detail while job is running', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('reloads questions.json when its producer node completes', async () => {
    let detailRequests = 0
    let artifactRequests = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/jobs/j1') {
        detailRequests += 1
        const completed = detailRequests > 1
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...mockDetail,
            job: {
              ...mockDetail.job,
              source_type: 'question',
              status: completed ? 'completed' : 'running',
            },
            nodes: [
              {
                ...mockDetail.nodes[0],
                node_key: 'fetch_questions',
                capability: 'fetch_questions',
                outputs: ['questions.json'],
                status: completed ? 'completed' : 'running',
                finished_at: completed ? '2026-06-18T10:00:00Z' : undefined,
              },
            ],
          }),
        })
      }
      if (url === '/api/jobs/j1/artifacts/questions.json') {
        artifactRequests += 1
        if (artifactRequests === 1) {
          return Promise.resolve({
            ok: false,
            status: 404,
            text: async () => JSON.stringify({ detail: 'Artifact not found' }),
          })
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            content: JSON.stringify({
              questions: [
                {
                  question_id: 'Q100',
                  normalized: { stem: '<p>Generated later</p>' },
                },
              ],
            }),
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    expect(await screen.findByText('Artifact not found')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })

    expect(await screen.findByText('Generated later')).toBeInTheDocument()
    expect(artifactRequests).toBe(2)
  })

  it('does not poll detail when job is completed', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'completed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('disables rerun and package for a running job', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(screen.getByLabelText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('打包')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('删除')).not.toHaveAttribute('disabled')
  })

  it('reruns a selected node and refreshes detail', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('重跑').click()
    })

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/nodes/extract/rerun',
        expect.objectContaining({ method: 'POST' })
      )
    })
  })

  it('packages a completed job and opens download URL', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const fetchMock = createFetchMock({
      detailStatus: 'completed',
      packageUrl: '/api/workspaces/ws1/packages/j1.zip',
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('打包').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workspaces/ws1/jobs/package',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ job_ids: ['j1'] }),
        })
      )
    })
    expect(openSpy).toHaveBeenCalledWith(
      '/api/workspaces/ws1/packages/j1.zip',
      '_blank'
    )
    openSpy.mockRestore()
  })

  it('deletes the job after confirm and navigates back to the list', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('删除').click()
    })

    expect(screen.getByText(/确定删除任务/)).toBeInTheDocument()

    await act(async () => {
      screen.getByText('删除').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1',
        expect.objectContaining({ method: 'DELETE' })
      )
    })
    expect(screen.getByTestId('job-list-page')).toBeInTheDocument()
  })

  it('runs to a selected target and refreshes detail', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()

    await act(async () => {
      const chip = screen.getByTestId('target-chip-review')
      fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/run-to',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ target_node_key: 'review' }),
        })
      )
    })
  })

  it('runs to a target from a selected start node', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    await act(async () => {
      const chip = screen.getByTestId('target-chip-review')
      fireEvent.click(chip)
    })

    await act(async () => {
      const chip = screen.getByTestId('start-chip-generate')
      fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/run-to',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            target_node_key: 'review',
            start_node_key: 'generate',
          }),
        })
      )
    })
  })

  it('shows continue full flow when paused with target_reached reason', async () => {
    const fetchMock = createFetchMock({
      detailStatus: 'paused',
      pauseReason: 'target_reached',
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByLabelText('继续完整流程')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('继续完整流程').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/continue',
        expect.objectContaining({ method: 'POST' })
      )
    })
  })

  it('renders actions as icon buttons in app bar, not text buttons in body', async () => {
    vi.stubGlobal('fetch', createFetchMock({ detailStatus: 'completed' }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(screen.getByLabelText('重跑')).toBeInTheDocument()
    expect(screen.getByLabelText('打包')).toBeInTheDocument()
    // The old body action bar used md-outlined-button with text labels;
    // app bar actions are now md-icon-button with aria-label.
    expect(
      screen.queryByText('重跑', { selector: 'md-outlined-button' })
    ).not.toBeInTheDocument()
  })

  it('opens artifact list after opening and closing fullscreen DAG dialog', async () => {
    vi.stubGlobal('fetch', createFetchMock({ detailStatus: 'completed' }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    // Open fullscreen DAG dialog
    await act(async () => {
      screen.getByLabelText('查看 DAG').click()
    })
    expect(await screen.findByLabelText('关闭')).toBeInTheDocument()

    // Close fullscreen DAG dialog
    await act(async () => {
      screen.getByLabelText('关闭').click()
    })
    await waitFor(() => {
      expect(screen.queryByLabelText('关闭')).not.toBeInTheDocument()
    })

    // Click artifact folder button in app-bar actions
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })

    // Artifact list dialog should open
    expect(
      screen.getByText('产物文件', { selector: '[slot="headline"]' })
    ).toBeInTheDocument()
    expect(screen.getByText('question.json')).toBeInTheDocument()
  })
})
