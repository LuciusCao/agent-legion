import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceDag from './WorkspaceDag'

function NavigateToMathDag() {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate('/workspaces/math/dag')}>
      切换到数学 DAG
    </button>
  )
}

const dagResponse = {
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
      status_counts: {
        pending: 1,
        running: 0,
        completed: 2,
        failed: 0,
        stale: 0,
      },
    },
  ],
}

describe('WorkspaceDag', () => {
  it('renders node status counts and links to runs by node', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => dagResponse,
      })
    )

    render(
      <MemoryRouter initialEntries={['/workspaces/math/dag']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/dag"
            element={<WorkspaceDag />}
          />
          <Route
            path="/workspaces/:workspaceId/runs"
            element={<div>Runs page</div>}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('题目内容生成')).toBeInTheDocument()
    expect(screen.getByText('fetch_question_context')).toBeInTheDocument()
    expect(screen.getByText(/completed 2/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('fetch_question_context'))
    expect(screen.getByText('Runs page')).toBeInTheDocument()
  })

  it('clears a previous load error after a later workspace fetch succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => 'CMS down',
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => dagResponse,
      })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/broken/dag']}>
        <NavigateToMathDag />
        <Routes>
          <Route
            path="/workspaces/:workspaceId/dag"
            element={<WorkspaceDag />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('HTTP 500: CMS down')).toBeInTheDocument()

    fireEvent.click(screen.getByText('切换到数学 DAG'))

    expect(await screen.findByText('题目内容生成')).toBeInTheDocument()
    expect(screen.queryByText('HTTP 500: CMS down')).not.toBeInTheDocument()
  })
})
