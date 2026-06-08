import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import WorkspaceJobDetail from './WorkspaceJobDetail'

describe('WorkspaceJobDetail', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders nodes and previews a JSON artifact', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          job: { id: 'j1', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q1', title: 'Q1', status: 'completed' },
          nodes: [{ id: 1, job_id: 'j1', node_key: 'fetch_question_context', status: 'completed', error_message: '' }],
          runs: [{ id: 1, job_id: 'j1', node_key: 'fetch_question_context', status: 'completed', command_json: '[]', exit_code: 0, log_path: '', error_message: '', started_at: '2026-06-08 10:00:00', finished_at: '2026-06-08 10:00:01' }],
          artifacts: ['question_context.json'],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ name: 'question_context.json', content: '{"question_id":"q1"}' }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route path="/workspaces/:workspaceId/jobs/:jobId" element={<WorkspaceJobDetail />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    expect(screen.getAllByText('fetch_question_context')).toHaveLength(2)
    fireEvent.click(screen.getByText('question_context.json'))
    expect(await screen.findByText(/"question_id": "q1"/)).toBeInTheDocument()
  })

  it('displays error when fetchJobDetail fails', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route path="/workspaces/:workspaceId/jobs/:jobId" element={<WorkspaceJobDetail />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText(/HTTP 500/)).toBeInTheDocument()
  })

  it('displays plain text artifact as-is', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          job: { id: 'j1', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q1', title: 'Q1', status: 'completed' },
          nodes: [],
          runs: [],
          artifacts: ['log.txt'],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ name: 'log.txt', content: 'plain text content' }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route path="/workspaces/:workspaceId/jobs/:jobId" element={<WorkspaceJobDetail />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    fireEvent.click(screen.getByText('log.txt'))
    expect(await screen.findByText('plain text content')).toBeInTheDocument()
  })
})
