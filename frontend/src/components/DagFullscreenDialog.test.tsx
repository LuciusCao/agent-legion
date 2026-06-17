import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { DagFullscreenDialog } from './DagFullscreenDialog'
import type { DagGraphNode, DagGraphEdge } from './DagGraph'
import * as jobApi from '../jobApi'

vi.mock('../jobApi')

const mockFetchJobLog = vi.mocked(jobApi.fetchJobLog)

const nodes: DagGraphNode[] = [
  { key: 'a', label: '提取', status: 'completed' },
  { key: 'b', label: '生成', status: 'running' },
]
const edges: DagGraphEdge[] = [{ from: 'a', to: 'b' }]

type DialogProps = Parameters<typeof DagFullscreenDialog>[0]

function renderDialog(props: Partial<DialogProps> = {}) {
  return render(
    <DagFullscreenDialog
      open={true}
      jobId="job-1"
      nodes={nodes}
      edges={edges}
      runs={[
        {
          id: 42,
          node_key: 'a',
          status: 'completed',
          started_at: '2026-06-17T00:00:00Z',
        },
      ]}
      onClose={vi.fn()}
      {...props}
    />
  )
}

describe('DagFullscreenDialog', () => {
  beforeEach(() => {
    mockFetchJobLog.mockReset()
  })

  it('renders nodes when open', () => {
    renderDialog()
    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('生成')).toBeInTheDocument()
    expect(screen.getByLabelText('关闭')).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    const { container } = render(
      <DagFullscreenDialog
        open={false}
        jobId="job-1"
        nodes={nodes}
        edges={edges}
        onClose={vi.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn()
    renderDialog({ onClose })
    fireEvent.click(screen.getByLabelText('关闭'))
    expect(onClose).toHaveBeenCalled()
  })

  it('opens the log dialog when 查看日志 is clicked', async () => {
    mockFetchJobLog.mockResolvedValue({
      run_id: 42,
      log: 'log line',
      truncated: false,
    })

    renderDialog()

    fireEvent.click(screen.getAllByTestId('dag-node')[0])
    fireEvent.click(screen.getByText('查看日志'))

    await waitFor(() => {
      expect(screen.getByText('日志 — 提取')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByText('log line')).toBeInTheDocument()
    })
    expect(mockFetchJobLog).toHaveBeenCalledWith('job-1', 42)
  })

  it('does not open the log dialog when the selected node has no run', () => {
    renderDialog()

    fireEvent.click(screen.getAllByTestId('dag-node')[1])
    fireEvent.click(screen.getByText('查看日志'))

    expect(screen.queryByText('日志 — 生成')).not.toBeInTheDocument()
    expect(mockFetchJobLog).not.toHaveBeenCalled()
  })
})
