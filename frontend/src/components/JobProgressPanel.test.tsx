import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { JobProgressPanel } from './JobProgressPanel'
import * as jobApi from '../jobApi'
import type { JobNode, NodeRun } from '../jobTypes'

vi.mock('../jobApi', () => ({
  fetchJobLog: vi.fn(),
  fetchRunTokenUsage: vi.fn(),
}))

const mockFetchJobLog = vi.mocked(jobApi.fetchJobLog)
const mockFetchRunTokenUsage = vi.mocked(jobApi.fetchRunTokenUsage)

const mockNodes: JobNode[] = [
  {
    id: 1,
    job_id: 'j1',
    node_key: 'extract',
    label: '提取',
    status: 'completed',
    after: [],
    created_at: '2026-06-09T07:59:00Z',
    started_at: '2026-06-09T08:00:00Z',
    finished_at: '2026-06-09T08:00:12Z',
    error_message: '',
    stale_reason: '',
    capability: 'extract',
    inputs: [],
    outputs: [],
    executor_kind: 'local',
    executor_id: 'local-1',
  },
  {
    id: 2,
    job_id: 'j1',
    node_key: 'generate',
    label: '生成',
    status: 'running',
    after: ['extract'],
    created_at: '2026-06-09T07:59:00Z',
    started_at: '2026-06-09T08:00:13Z',
    error_message: '',
    stale_reason: '',
    capability: 'generate',
    inputs: [],
    outputs: [],
    executor_kind: 'pi',
    executor_id: 'pi-1',
  },
]

const mockRuns: NodeRun[] = [
  {
    id: 1,
    job_id: 'j1',
    node_key: 'extract',
    status: 'completed',
    started_at: '2026-06-09T08:00:00Z',
    finished_at: '2026-06-09T08:00:12Z',
    command_json: '[]',
    exit_code: 0,
    log_path: '/logs/jobs/run.log',
    error_message: '',
    run_dir: '',
    session_dir: '',
    runner: '',
  },
]

describe('JobProgressPanel', () => {
  beforeEach(() => {
    mockFetchJobLog.mockReset()
    mockFetchRunTokenUsage
      .mockReset()
      .mockImplementation(() => new Promise(() => {}))
  })

  it('renders node labels and statuses', () => {
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )

    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成').length).toBeGreaterThanOrEqual(1)
  })

  it('opens JobLogDialog when view log is clicked', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'log content',
      truncated: false,
    })

    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )

    fireEvent.click(screen.getByText('查看日志'))
    await waitFor(() => {
      expect(screen.getByText('log content')).toBeInTheDocument()
    })
  })

  it('does not render log_path in the timeline', () => {
    const { container } = render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )

    expect(container.textContent).not.toContain('/logs/jobs/run.log')
  })

  it('fetches log by job and run identity', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'log content',
      truncated: false,
    })

    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )

    fireEvent.click(screen.getByText('查看日志'))
    await waitFor(() => {
      expect(mockFetchJobLog).toHaveBeenCalledWith('j1', 1)
    })
  })

  it('does not render stage title', () => {
    const { container } = render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    expect(container.textContent).not.toContain('阶段明细')
  })

  it('renders stale node with pending visual and label', () => {
    const staleNodes = [
      {
        ...mockNodes[0],
        node_key: 'review',
        label: '审核',
        status: 'stale',
        started_at: '2026-06-09T08:00:00Z',
        finished_at: '2026-06-09T08:00:05Z',
      },
    ]
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={staleNodes}
        runs={[]}
        onOpenDagDialog={vi.fn()}
      />
    )
    expect(screen.getByText('审核')).toBeInTheDocument()
    expect(screen.getByText('已过期')).toBeInTheDocument()
  })

  it('shows wait time instead of full datetime', () => {
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    expect(
      screen.getAllByText(/\d+时\d+分\d+秒|\d+分\d+秒|\d+秒/).length
    ).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/2026\/6\/9 08:00:00/)).not.toBeInTheDocument()
  })

  it('computes downstream wait time from dependency finish, not job creation', () => {
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    // generate started 13s after job creation, but only 1s after extract finished.
    expect(screen.getByText('1秒')).toBeInTheDocument()
  })

  it('uses node created_at instead of job created_at for wait time', () => {
    const rerunNodes = [
      {
        ...mockNodes[0],
        created_at: '2026-06-10T08:00:00Z',
        started_at: '2026-06-10T08:00:01Z',
      },
    ]
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={rerunNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    // Wait time should be 1s (from node created_at to started_at), not ~29h.
    expect(screen.getByText('1秒')).toBeInTheDocument()
  })

  it('renders executor kind labels for local and agent nodes', () => {
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    expect(screen.getByText('本地')).toBeInTheDocument()
    expect(screen.getByText('Pi Agent')).toBeInTheDocument()
  })

  it('hides old run errors and logs after a node rerun', () => {
    const rerunNodes = [
      {
        ...mockNodes[0],
        node_key: 'generate',
        label: '生成',
        status: 'stale',
        created_at: '2026-06-10T08:00:00Z',
        started_at: null,
        finished_at: null,
        error_message: '',
      },
    ]
    const oldFailedRuns = [
      {
        ...mockRuns[0],
        id: 2,
        node_key: 'generate',
        status: 'failed',
        started_at: '2026-06-09T08:00:00Z',
        finished_at: '2026-06-09T08:00:05Z',
        error_message: 'previous failure',
        log_path: '/logs/old.log',
      },
    ]
    render(
      <JobProgressPanel
        jobId="j1"
        nodes={rerunNodes}
        runs={oldFailedRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    expect(screen.queryByText('错误详情')).not.toBeInTheDocument()
    expect(screen.queryByText('查看日志')).not.toBeInTheDocument()
  })

  it('shows compact token summary for each run after loading', async () => {
    mockFetchRunTokenUsage.mockResolvedValue({
      job_id: 'j1',
      run_id: 1,
      usage: {
        node_run_id: 1,
        node_key: 'extract',
        provider: 'openai',
        model: 'gpt-4o',
        skill_version: 'v1.2.3',
        message_count: 3,
        input_tokens: 1000,
        output_tokens: 200,
        cache_read_tokens: 50,
        total_tokens: 1250,
        cost: {
          input: 0.005,
          output: 0.003,
          cache_read: 0.0001,
          total: 0.0081,
          currency: 'USD',
        },
        pricing_missing: false,
        is_complete: true,
        usage_source: 'events_jsonl',
      },
      reason: null,
    })

    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    expect(screen.getByText(/Token: 1,250/)).toBeInTheDocument()
    expect(screen.getByText(/USD 0.0081/)).toBeInTheDocument()
  })

  it('expands token usage details when compact summary is clicked', async () => {
    mockFetchRunTokenUsage.mockResolvedValue({
      job_id: 'j1',
      run_id: 1,
      usage: {
        node_run_id: 1,
        node_key: 'extract',
        provider: 'openai',
        model: 'gpt-4o',
        skill_version: 'v1.2.3',
        message_count: 3,
        input_tokens: 1000,
        output_tokens: 200,
        cache_read_tokens: 50,
        total_tokens: 1250,
        cost: {
          input: 0.005,
          output: 0.003,
          cache_read: 0.0001,
          total: 0.0081,
          currency: 'USD',
        },
        pricing_missing: false,
        is_complete: true,
        usage_source: 'events_jsonl',
      },
      reason: null,
    })

    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    fireEvent.click(screen.getByLabelText('Token 用量'))

    expect(screen.getByText('openai / gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('v1.2.3')).toBeInTheDocument()
    expect(screen.getByText('Token 明细')).toBeInTheDocument()
    expect(screen.getByText('费用明细')).toBeInTheDocument()
  })

  it('does not block log button when token usage is missing', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'log content',
      truncated: false,
    })
    mockFetchRunTokenUsage.mockResolvedValue({
      job_id: 'j1',
      run_id: 1,
      usage: null,
      reason: 'no token usage recorded for run',
    })

    render(
      <JobProgressPanel
        jobId="j1"
        nodes={mockNodes}
        runs={mockRuns}
        onOpenDagDialog={vi.fn()}
      />
    )
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    fireEvent.click(screen.getByText('查看日志'))
    await waitFor(() => {
      expect(screen.getByText('log content')).toBeInTheDocument()
    })
  })
})
