import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobDetailPage from './JobDetailPage'

const mockDetail = {
  job: {
    id: 'j1',
    workspace_id: 'ws1',
    pipeline_key: 'question_content',
    source_id: 'Q100',
    title: 'Algebra Problem',
    status: 'running',
  },
  nodes: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      label: 'extract',
      status: 'completed',
      after: [],
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      error_message: '',
    },
    {
      id: 2,
      job_id: 'j1',
      node_key: 'generate',
      label: 'generate',
      status: 'running',
      after: ['extract'],
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

describe('JobDetailPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders page with job detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )

    renderPage()

    expect(await screen.findByText('节点进度')).toBeInTheDocument()
    expect(screen.getAllByText('extract').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('generate').length).toBeGreaterThanOrEqual(1)
  })

  it('opens fullscreen DAG dialog from progress panel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )

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

  it('clicking a timeline node shows NodeDetailPanel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )

    renderPage()

    const extractBtn = await screen.findByRole('button', { name: /extract/ })
    fireEvent.click(extractBtn)

    const panel = await screen.findByTestId('node-detail-panel')
    expect(panel).toBeInTheDocument()
    expect(within(panel).getByText('extract')).toBeInTheDocument()
    expect(screen.getByText('12秒')).toBeInTheDocument()
  })
})
