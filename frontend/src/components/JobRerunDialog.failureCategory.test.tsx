import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { JobRerunDialog } from './JobRerunDialog'
import { makeJob } from '../testing/fixtures'
import type { WorkflowDefinitionRecord } from '../types'
import type { FailedNodeRunItem } from '../types/failureTypes'
import { fetchFailedNodeRuns } from '../api'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api', () => ({
  fetchFailedNodeRuns: vi.fn(),
}))

const mockFetchFailedNodeRuns = vi.mocked(fetchFailedNodeRuns)

const workflow: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  intake: { modes: [] },
  edges: [],
  nodes: [
    {
      key: 'extract',
      label: '提取',
      after: [],
      capability: 'extract',
      inputs: [] as string[],
      outputs: [] as string[],
    },
    {
      key: 'review',
      label: '审核',
      after: ['extract'],
      capability: 'review',
      inputs: [] as string[],
      outputs: [] as string[],
    },
  ],
}

function makeRun(overrides: Partial<FailedNodeRunItem>): FailedNodeRunItem {
  return {
    job_id: 'j1',
    node_key: 'extract',
    node_run_id: 1,
    workflow_key: 'question_content',
    failure_category: 'technical',
    failure_detail: 'timeout',
    error_message: 'boom',
    finished_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

function renderDialog(
  overrides: Partial<Parameters<typeof JobRerunDialog>[0]> = {}
) {
  return renderWithClient(
    <JobRerunDialog
      open
      allowFailedNodeMode
      failureContext={{ workspaceId: 'ws1', workflowKey: 'question_content' }}
      jobs={[
        makeJob({ id: 'j1', status: 'failed', source_id: 'Q1' }),
        makeJob({ id: 'j2', status: 'failed', source_id: 'Q2' }),
        makeJob({ id: 'j3', status: 'failed', source_id: 'Q3' }),
      ]}
      workflowDefinition={workflow}
      onConfirm={vi.fn().mockResolvedValue(undefined)}
      onClose={vi.fn()}
      {...overrides}
    />
  )
}

// workflow_key 需与测试 workflow 匹配，否则对话框拿不到节点列表。
const workflowJobs = () => [
  makeJob({
    id: 'j1',
    status: 'failed',
    source_id: 'Q1',
    workspace_id: 'question_content',
  }),
  makeJob({
    id: 'j2',
    status: 'failed',
    source_id: 'Q2',
    workspace_id: 'question_content',
  }),
  makeJob({
    id: 'j3',
    status: 'failed',
    source_id: 'Q3',
    workspace_id: 'question_content',
  }),
]

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

describe('JobRerunDialog failure category mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lazily loads category counts only after failed-node mode activates', async () => {
    mockFetchFailedNodeRuns.mockResolvedValue({ runs: [] })
    renderDialog()

    expect(mockFetchFailedNodeRuns).not.toHaveBeenCalled()

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await waitFor(() =>
      expect(mockFetchFailedNodeRuns).toHaveBeenCalledWith('ws1', {
        workflowKey: 'question_content',
      })
    )
  })

  it('shows per-category counts, disables empty categories, and confirms with category', async () => {
    mockFetchFailedNodeRuns.mockResolvedValue({
      runs: [
        makeRun({ job_id: 'j1', failure_category: 'technical' }),
        makeRun({
          job_id: 'j1',
          node_key: 'review',
          node_run_id: 2,
          failure_category: 'business',
          finished_at: '2026-06-01T00:00:00Z',
        }),
        makeRun({ job_id: 'j2', failure_category: 'business' }),
      ],
    })
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    renderDialog({ onConfirm, onClose })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await waitFor(() =>
      expect(screen.getByTestId('rerun-category-technical')).toHaveTextContent(
        '技术性失败 (1)'
      )
    )
    expect(screen.getByTestId('rerun-category-business')).toHaveTextContent(
      '业务性失败 (1)'
    )
    expect(screen.getByTestId('rerun-category-unknown')).toHaveClass(
      'Mui-disabled'
    )

    act(() => {
      screen.getByTestId('rerun-category-technical').click()
    })

    expect(
      screen.getByText(
        '已选择 3 个任务，其中 1 个含技术性失败，将重跑失败节点本身'
      )
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByRole('button', { name: '重跑 1 个技术性失败' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(
      null,
      true,
      ['j1', 'j2', 'j3'],
      'technical'
    )
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the legacy all-failures path unchanged', async () => {
    mockFetchFailedNodeRuns.mockResolvedValue({ runs: [] })
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderDialog({ onConfirm })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await act(async () => {
      screen.getByRole('button', { name: '重跑 3 个失败任务' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(null, true)
  })

  it('passes the selected start node together with the failure category', async () => {
    mockFetchFailedNodeRuns.mockResolvedValue({
      runs: [makeRun({ job_id: 'j1', failure_category: 'technical' })],
    })
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderDialog({ onConfirm, jobs: workflowJobs() })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    // “全部失败”下起始节点选择不可用。
    expect(screen.getByTestId('rerun-from-node-review')).toHaveClass(
      'Mui-disabled'
    )

    await waitFor(() =>
      expect(screen.getByTestId('rerun-category-technical')).toHaveTextContent(
        '技术性失败 (1)'
      )
    )

    act(() => {
      screen.getByTestId('rerun-category-technical').click()
    })
    act(() => {
      screen.getByTestId('rerun-from-node-review').click()
    })

    await act(async () => {
      screen.getByRole('button', { name: '重跑 1 个技术性失败' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(
      null,
      true,
      ['j1', 'j2', 'j3'],
      'technical',
      'review'
    )
  })

  it('omits the start node when the auto option is kept', async () => {
    mockFetchFailedNodeRuns.mockResolvedValue({
      runs: [makeRun({ job_id: 'j1', failure_category: 'technical' })],
    })
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderDialog({ onConfirm, jobs: workflowJobs() })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await waitFor(() =>
      expect(screen.getByTestId('rerun-category-technical')).toHaveTextContent(
        '技术性失败 (1)'
      )
    )

    act(() => {
      screen.getByTestId('rerun-category-technical').click()
    })
    // 选中起始节点后切回“自动”，不应携带 fromNodeKey。
    act(() => {
      screen.getByTestId('rerun-from-node-extract').click()
    })
    act(() => {
      screen.getByTestId('rerun-from-node-auto').click()
    })

    await act(async () => {
      screen.getByRole('button', { name: '重跑 1 个技术性失败' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(
      null,
      true,
      ['j1', 'j2', 'j3'],
      'technical'
    )
  })

  it('degrades silently without counts when the fetch fails', async () => {
    mockFetchFailedNodeRuns.mockRejectedValue(new Error('network down'))
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderDialog({ onConfirm })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await waitFor(() => expect(mockFetchFailedNodeRuns).toHaveBeenCalled())
    expect(screen.getByTestId('rerun-category-technical')).toHaveTextContent(
      '技术性失败'
    )
    expect(screen.getByTestId('rerun-category-technical')).not.toHaveClass(
      'Mui-disabled'
    )

    act(() => {
      screen.getByTestId('rerun-category-business').click()
    })

    expect(
      screen.getByText(/业务性失败：评审不通过，将从上游节点重跑/)
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByRole('button', { name: '重跑业务性失败' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(
      null,
      true,
      ['j1', 'j2', 'j3'],
      'business'
    )
  })

  it('does not fetch counts without a failureContext', async () => {
    renderDialog({ failureContext: undefined })

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    expect(mockFetchFailedNodeRuns).not.toHaveBeenCalled()
    expect(screen.getByTestId('rerun-category-technical')).toHaveTextContent(
      '技术性失败'
    )
  })
})
