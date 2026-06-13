import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { JobProgressPanel } from './JobProgressPanel'
import * as jobApi from '../jobApi'

vi.mock('../jobApi')

const mockFetchJobLog = vi.mocked(jobApi.fetchJobLog)

const mockNodes = [
  {
    id: 1,
    job_id: 'j1',
    node_key: 'extract',
    label: '提取',
    status: 'completed',
    after: [],
    started_at: '2026-06-09T08:00:00Z',
    finished_at: '2026-06-09T08:00:12Z',
    error_message: '',
    stale_reason: '',
    capability: 'extract',
    inputs: [],
    outputs: [],
  },
  {
    id: 2,
    job_id: 'j1',
    node_key: 'generate',
    label: '生成',
    status: 'running',
    after: ['extract'],
    started_at: '2026-06-09T08:00:13Z',
    error_message: '',
    stale_reason: '',
    capability: 'generate',
    inputs: [],
    outputs: [],
  },
]

const mockRuns = [
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
  },
]

describe('JobProgressPanel', () => {
  beforeEach(() => {
    mockFetchJobLog.mockReset()
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
})
