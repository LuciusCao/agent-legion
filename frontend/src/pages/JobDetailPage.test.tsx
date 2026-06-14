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
    pipeline_key: 'question_content',
    source_id: 'Q100',
    source_type: 'knowledge',
    title: 'Algebra Problem',
    status: 'running',
  },
  nodes: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      label: '提取',
      status: 'completed',
      capability: 'extract',
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
      status: 'pending',
      capability: 'review',
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

    expect(await screen.findByText('节点进度')).toBeInTheDocument()
    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(2)
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
    expect(container.querySelectorAll('[data-node]')).toHaveLength(3)
    expect(container.querySelectorAll('path[data-testid="edge"]')).toHaveLength(
      2
    )
  })

  it('polls detail while job is running', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not poll detail when job is completed', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'completed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('节点进度')).toBeInTheDocument()
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="target-chip-review"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="target-chip-review"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
    })

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="start-chip-generate"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
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
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

    expect(screen.getByLabelText('重跑')).toBeInTheDocument()
    expect(screen.getByLabelText('打包')).toBeInTheDocument()
    expect(
      screen.queryByText('重跑', { selector: 'md-outlined-button' })
    ).not.toBeInTheDocument()
  })
})
