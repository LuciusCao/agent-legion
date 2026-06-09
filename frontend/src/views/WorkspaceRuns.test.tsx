import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceRuns from './WorkspaceRuns'

const runsResponse = {
  runs: [
    {
      id: 1,
      workspace_id: 'math',
      job_id: 'job-1',
      job_title: 'Question Q001',
      source_id: 'Q001',
      source_type: 'question',
      pipeline_key: 'question_content',
      node_key: 'fetch_question_context',
      status: 'completed',
      command_json: '[]',
      exit_code: 0,
      log_path: 'run.log',
      error_message: '',
      started_at: '2026-06-09 10:00:00',
      finished_at: '2026-06-09 10:00:01',
    },
  ],
}

describe('WorkspaceRuns', () => {
  it('renders workspace runs and opens the job detail page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => runsResponse,
      })
    )

    render(
      <MemoryRouter initialEntries={['/workspaces/math/runs']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/runs"
            element={<WorkspaceRuns />}
          />
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<div>Job detail page</div>}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Question Q001')).toBeInTheDocument()
    expect(screen.getByText(/fetch_question_context/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Question Q001'))
    expect(screen.getByText('Job detail page')).toBeInTheDocument()
  })

  it('clears a previous load error after a later filtered fetch succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => 'CMS down',
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => runsResponse,
      })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math/runs']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/runs"
            element={<WorkspaceRuns />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('HTTP 500: CMS down')).toBeInTheDocument()

    const select = screen.getByLabelText('Run status')
    Object.defineProperty(select, 'value', {
      value: 'failed',
      configurable: true,
    })
    fireEvent.input(select)

    expect(await screen.findByText('Question Q001')).toBeInTheDocument()
    expect(screen.queryByText('HTTP 500: CMS down')).not.toBeInTheDocument()
  })
})
