import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceDag from './WorkspaceDag'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

describe('WorkspaceDag', () => {
  it('renders node status counts and links to runs by node', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
          },
          nodes: [
            {
              key: 'fetch_question_context',
              runner: 'local',
              after: [],
              inputs: [],
              outputs: ['question_context.json'],
              status_counts: { pending: 1, running: 0, completed: 2, failed: 0, stale: 0 },
            },
          ],
        }),
      })
    )

    render(
      <MemoryRouter initialEntries={['/workspaces/math/dag']}>
        <Routes>
          <Route path="/workspaces/:workspaceId/dag" element={<WorkspaceDag />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('题目内容生成')).toBeInTheDocument()
    expect(screen.getByText('fetch_question_context')).toBeInTheDocument()
    expect(screen.getByText(/completed 2/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('fetch_question_context'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/math/runs?node_key=fetch_question_context')
  })
})
