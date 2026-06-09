import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceRuns from './WorkspaceRuns'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

describe('WorkspaceRuns', () => {
  it('renders workspace runs and opens the job detail page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
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
        }),
      })
    )

    render(
      <MemoryRouter initialEntries={['/workspaces/math/runs']}>
        <Routes>
          <Route path="/workspaces/:workspaceId/runs" element={<WorkspaceRuns />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Question Q001')).toBeInTheDocument()
    expect(screen.getByText(/fetch_question_context/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Question Q001'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/math/jobs/job-1')
  })
})
