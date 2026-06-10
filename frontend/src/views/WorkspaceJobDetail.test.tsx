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
          job: {
            id: 'j1',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'q1',
            title: 'Q1',
            status: 'completed',
          },
          nodes: [
            {
              id: 1,
              job_id: 'j1',
              node_key: 'fetch_question_context',
              status: 'completed',
              error_message: '',
            },
          ],
          runs: [
            {
              id: 1,
              job_id: 'j1',
              node_key: 'fetch_question_context',
              status: 'completed',
              command_json: '[]',
              exit_code: 0,
              log_path: '',
              error_message: '',
              started_at: '2026-06-08 10:00:00',
              finished_at: '2026-06-08 10:00:01',
              run_dir: '',
              session_dir: '',
            },
          ],
          artifacts: ['question_context.json'],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          name: 'question_context.json',
          content: '{"question_id":"q1"}',
        }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
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
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
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
          job: {
            id: 'j1',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'q1',
            title: 'Q1',
            status: 'completed',
          },
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
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    fireEvent.click(screen.getByText('log.txt'))
    expect(await screen.findByText('plain text content')).toBeInTheDocument()
  })

  it('renders Pi badge and session for agent runs', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job: {
          id: 'j1',
          workspace_id: 'math_ws',
          pipeline_key: 'reading_analysis',
          source_id: 'q1',
          title: 'Q1',
          status: 'completed',
        },
        nodes: [
          {
            id: 1,
            job_id: 'j1',
            node_key: 'extract_keywords',
            status: 'completed',
            error_message: '',
          },
        ],
        runs: [
          {
            id: 1,
            job_id: 'j1',
            node_key: 'extract_keywords',
            status: 'completed',
            command_json: JSON.stringify([
              'pi',
              '--mode',
              'json',
              '--session-dir',
              '/data/jobs/j1/runs/extract_keywords/r1/session',
            ]),
            exit_code: 0,
            log_path: '/data/jobs/j1/runs/extract_keywords/r1/events.jsonl',
            error_message: '',
            started_at: '2026-06-08 10:00:00',
            finished_at: '2026-06-08 10:00:01',
            run_dir: '/data/jobs/j1/runs/extract_keywords/r1',
            session_dir: '/data/jobs/j1/runs/extract_keywords/r1/session',
          },
        ],
        artifacts: ['keywords_raw.json'],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    expect(screen.getByText('Pi')).toBeInTheDocument()
    expect(screen.getByText(/session: session/)).toBeInTheDocument()
  })

  it('does not render Pi badge for non-pi commands', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job: {
          id: 'j1',
          workspace_id: 'math_ws',
          pipeline_key: 'reading_analysis',
          source_id: 'q1',
          title: 'Q1',
          status: 'completed',
        },
        nodes: [],
        runs: [
          {
            id: 1,
            job_id: 'j1',
            node_key: 'fetch_questions',
            status: 'completed',
            command_json: JSON.stringify(['python', 'script.py']),
            exit_code: 0,
            log_path: '',
            error_message: '',
            started_at: '2026-06-08 10:00:00',
            finished_at: '2026-06-08 10:00:01',
            run_dir: '/data/j1/r1',
            session_dir: '',
          },
        ],
        artifacts: [],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    expect(screen.queryByText('Pi')).not.toBeInTheDocument()
  })

  it('handles empty or invalid command_json gracefully', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job: {
          id: 'j1',
          workspace_id: 'math_ws',
          pipeline_key: 'reading_analysis',
          source_id: 'q1',
          title: 'Q1',
          status: 'completed',
        },
        nodes: [],
        runs: [
          {
            id: 1,
            job_id: 'j1',
            node_key: 'fetch_questions',
            status: 'completed',
            command_json: '',
            exit_code: 0,
            log_path: '',
            error_message: '',
            started_at: '2026-06-08 10:00:00',
            finished_at: '2026-06-08 10:00:01',
            run_dir: '/data/j1/r1',
            session_dir: '/data/j1/r1/session/',
          },
        ],
        artifacts: [],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    expect(screen.queryByText('Pi')).not.toBeInTheDocument()
    // session_dir is hidden for non-Pi runs
    expect(screen.queryByText(/session:/)).not.toBeInTheDocument()
  })

  it('renders session basename with trailing slash', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job: {
          id: 'j1',
          workspace_id: 'math_ws',
          pipeline_key: 'reading_analysis',
          source_id: 'q1',
          title: 'Q1',
          status: 'completed',
        },
        nodes: [],
        runs: [
          {
            id: 1,
            job_id: 'j1',
            node_key: 'extract_keywords',
            status: 'completed',
            command_json: JSON.stringify([
              'pi',
              '--mode',
              'json',
              '--session-dir',
              '/data/jobs/j1/runs/extract_keywords/r1/session/',
            ]),
            exit_code: 0,
            log_path: '',
            error_message: '',
            started_at: '2026-06-08 10:00:00',
            finished_at: '2026-06-08 10:00:01',
            run_dir: '/data/jobs/j1/runs/extract_keywords/r1',
            session_dir: '/data/jobs/j1/runs/extract_keywords/r1/session/',
          },
        ],
        artifacts: [],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/workspaces/math_ws/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<WorkspaceJobDetail />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Q1')).toBeInTheDocument()
    expect(screen.getByText(/session: session/)).toBeInTheDocument()
  })
})
