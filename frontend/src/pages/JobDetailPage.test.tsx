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

function renderPage(initialEntry = '/workspaces/ws1/jobs/j1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
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
    expect(container.querySelectorAll('[data-node]')).toHaveLength(2)
    expect(container.querySelectorAll('path[data-testid="edge"]')).toHaveLength(
      1
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

    expect(screen.getByText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByText('打包')).toHaveAttribute('disabled')
    expect(screen.getByText('删除')).not.toHaveAttribute('disabled')
  })

  it('reruns a selected node and refreshes detail', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByText('重跑').click()
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
      screen.getByText('打包').click()
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

  it('deletes the job and navigates back to the list', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('节点进度')).toBeInTheDocument()
    })

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
})
