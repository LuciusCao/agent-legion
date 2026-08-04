import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  cleanup,
} from '@testing-library/react'
import { JobLogDialog } from './JobLogDialog'
import * as jobApi from '../../api/jobApi'
import type { JobLogResponse } from '../../types/jobTypes'
import styles from './JobLogDialog.module.css'

vi.mock('../../api/jobApi')

const mockFetchJobLog = vi.mocked(jobApi.fetchJobLog)

describe('JobLogDialog', () => {
  beforeEach(() => {
    mockFetchJobLog.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    cleanup()
  })

  it('shows loading state while fetching', async () => {
    let resolve: (value: JobLogResponse) => void = () => {}
    mockFetchJobLog.mockImplementation(
      () =>
        new Promise((res) => {
          resolve = res
        })
    )

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('加载中...')).toBeInTheDocument()

    await act(async () => {
      resolve({ run_id: 1, log: 'ok', truncated: false })
    })
  })

  it('renders log content when loaded', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'line1\nline2',
      truncated: false,
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      const pre = document.querySelector('pre')
      expect(pre).toHaveTextContent('line1')
    })
    expect(document.querySelector('pre')).toHaveTextContent('line2')
  })

  it('renders empty state when log is empty', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: '',
      truncated: false,
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('暂无日志')).toBeInTheDocument()
    })
  })

  it('shows truncated hint when response is truncated', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'tail',
      truncated: true,
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(
        screen.getByText('仅显示尾部日志，完整内容已截断')
      ).toBeInTheDocument()
    })
  })

  it('renders error message on failure', async () => {
    mockFetchJobLog.mockRejectedValue(new Error('network error'))

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('network error')).toBeInTheDocument()
    })
  })

  it('cancels stale request when props change', async () => {
    const responses: Array<{
      resolve: (value: JobLogResponse) => void
      reject: (reason: Error) => void
    }> = []
    mockFetchJobLog.mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          responses.push({ resolve, reject })
        })
    )

    const { rerender } = render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    rerender(
      <JobLogDialog
        jobId="j1"
        runId={2}
        nodeLabel="generate"
        open={true}
        onClose={vi.fn()}
      />
    )

    await act(async () => {
      responses[0].resolve({ run_id: 1, log: 'stale', truncated: false })
      responses[1].resolve({ run_id: 2, log: 'fresh', truncated: false })
    })

    await waitFor(() => {
      expect(screen.getByText('fresh')).toBeInTheDocument()
    })
    expect(screen.queryByText('stale')).not.toBeInTheDocument()
  })

  it('cancels stale request when dialog closes and reopens with a different run', async () => {
    const responses: Array<{
      resolve: (value: JobLogResponse) => void
      reject: (reason: Error) => void
    }> = []
    mockFetchJobLog.mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          responses.push({ resolve, reject })
        })
    )

    const { rerender } = render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('加载中...')).toBeInTheDocument()

    rerender(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={false}
        onClose={vi.fn()}
      />
    )

    rerender(
      <JobLogDialog
        jobId="j1"
        runId={2}
        nodeLabel="generate"
        open={true}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('加载中...')).toBeInTheDocument()

    await act(async () => {
      responses[0].resolve({ run_id: 1, log: 'stale', truncated: false })
    })

    expect(screen.queryByText('stale')).not.toBeInTheDocument()
    expect(screen.getByText('加载中...')).toBeInTheDocument()

    await act(async () => {
      responses[1].resolve({ run_id: 2, log: 'fresh', truncated: false })
    })

    await waitFor(() => {
      expect(screen.getByText('fresh')).toBeInTheDocument()
    })
    expect(screen.queryByText('stale')).not.toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'ok',
      truncated: false,
    })
    const onClose = vi.fn()

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={onClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('ok')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('关闭'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('never renders log_path', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'ok',
      truncated: false,
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('ok')).toBeInTheDocument()
    })
    expect(document.body.textContent).not.toContain('/logs/jobs/')
    expect(document.body.textContent).not.toContain('.log')
  })

  it('renders structured entries without raw log path', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: '## Turn 1 · 思考\nhello',
      truncated: false,
      structured: [
        {
          type: 'thinking',
          title: 'Turn 1 · 思考',
          detail: 'hello',
          truncated: false,
        },
      ],
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    const toggle = await screen.findByRole('button', {
      name: 'Turn 1 · 思考',
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('hello')).not.toBeInTheDocument()

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('hello')).toBeInTheDocument()

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('hello')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('/logs/jobs/')
  })

  it('downloads rendered log as plain text when raw button is clicked', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:dummy')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      createObjectURL,
      revokeObjectURL,
    })

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click')

    mockFetchJobLog.mockResolvedValue({
      run_id: 1,
      log: 'readable log',
      truncated: false,
      structured: [
        {
          type: 'session',
          title: 'Agent 开始运行',
          detail: '',
          truncated: false,
        },
      ],
    })

    render(
      <JobLogDialog
        jobId="j1"
        runId={1}
        nodeLabel="extract"
        open={true}
        onClose={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('原始日志')).toBeInTheDocument()
    })
    expect(document.querySelector(`.${styles.entryDetail}`)).toBeNull()
    fireEvent.click(screen.getByText('原始日志'))

    expect(createObjectURL).toHaveBeenCalledOnce()
    const call = createObjectURL.mock.calls[0] as unknown as [Blob]
    expect(call[0].type).toBe('text/plain;charset=utf-8')
    expect(clickSpy).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:dummy')

    clickSpy.mockRestore()
  })
})
