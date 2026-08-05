import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import type { ReactElement } from 'react'
import { JobRerunDialog } from './JobRerunDialog'
import { makeJob } from '../testing/fixtures'
import type { WorkflowDefinitionRecord } from '../types'
import { TestQueryProvider } from '../testing/testQueryClient'

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
      key: 'generate',
      label: '生成',
      after: ['extract'],
      capability: 'generate',
      inputs: [] as string[],
      outputs: [] as string[],
    },
    {
      key: 'review',
      label: '审核',
      after: ['generate'],
      capability: 'review',
      inputs: [] as string[],
      outputs: [] as string[],
    },
  ],
}

const workflowNodesByKey: Record<string, WorkflowDefinitionRecord> = {
  question_content: workflow,
  other_workflow: {
    key: 'other_workflow',
    label: 'Other',
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
        key: 'convert',
        label: '转换',
        after: ['extract'],
        capability: 'convert',
        inputs: [] as string[],
        outputs: [] as string[],
      },
    ],
  },
}

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

describe('JobRerunDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nodes in workflow-definition order', () => {
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()
    expect(screen.getByTestId('rerun-chip-extract')).toHaveTextContent('提取')
    expect(screen.getByTestId('rerun-chip-generate')).toHaveTextContent('生成')
    expect(screen.getByTestId('rerun-chip-review')).toHaveTextContent('审核')
  })

  it('shows common node intersection for batch selections across workflows', () => {
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            workflow_key: 'other_workflow',
          }),
        ]}
        workflowDefinition={workflow}
        workflowNodesByKey={workflowNodesByKey}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByTestId('rerun-chip-extract')).toHaveTextContent('提取')
    expect(screen.queryByTestId('rerun-chip-generate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rerun-chip-review')).not.toBeInTheDocument()
  })

  it('identifies jobs excluded by workflow mismatch for the selected node', () => {
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
            source_id: 'Q1',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            workflow_key: 'unknown',
            source_id: 'Q2',
          }),
        ]}
        workflowDefinition={workflow}
        workflowNodesByKey={workflowNodesByKey}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    const generateChip = screen.getByTestId('rerun-chip-generate')
    expect(generateChip).toBeInTheDocument()
    act(() => {
      generateChip.click()
    })
    expect(screen.getByText(/以下任务不包含所选节点/)).toBeInTheDocument()
    expect(screen.getByText('Q2')).toBeInTheDocument()
  })

  it('calls onConfirm with the backend-authoritative node key', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    const reviewChip = screen.getByTestId('rerun-chip-review')
    act(() => {
      reviewChip.click()
    })

    await act(async () => {
      screen.getByText(/确认重跑/).click()
    })

    expect(onConfirm).toHaveBeenCalledWith('review', false)
    expect(onClose).toHaveBeenCalled()
  })

  it('counts and submits only jobs that have executed the selected node', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderWithClient(
      <JobRerunDialog
        open
        allowFailedNodeMode
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            workflow_key: 'question_content',
            node_summaries: [
              {
                node_key: 'generate',
                label: '生成',
                status: 'completed',
                error_message: '',
              },
            ],
          }),
          makeJob({
            id: 'j2',
            status: 'queued',
            workflow_key: 'question_content',
            node_summaries: [
              {
                node_key: 'generate',
                label: '生成',
                status: 'stale',
                error_message: '',
              },
            ],
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />
    )

    act(() => {
      screen.getByTestId('rerun-chip-generate').click()
    })

    expect(
      screen.getByText('已选择 2 个任务，可重跑 1 个，1 个尚未执行到所选节点')
    ).toBeInTheDocument()
    expect(
      screen.getByText('1 个任务尚未执行到所选节点，不能重跑。')
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByRole('button', { name: '重跑 1 个任务' }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith('generate', false, ['j1'])
  })

  it('calls onClose when cancel is clicked', () => {
    const onClose = vi.fn()
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        workflowDefinition={workflow}
        onConfirm={vi.fn()}
        onClose={onClose}
      />
    )

    act(() => {
      screen.getByText('取消').click()
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('renders nothing when not open', () => {
    const { container } = renderWithClient(
      <JobRerunDialog
        open={false}
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        workflowDefinition={workflow}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders failed-node chip and calls onConfirm with fromFailedNode', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    renderWithClient(
      <JobRerunDialog
        open
        allowFailedNodeMode
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            workflow_key: 'question_content',
            source_id: 'Q2',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    const failedChip = screen.getByTestId('rerun-chip-failed-node')
    expect(failedChip).toHaveTextContent('失败的节点')

    act(() => {
      failedChip.click()
    })

    expect(screen.getByText(/以下任务未失败，将被跳过/)).toBeInTheDocument()
    expect(screen.getByText('Q2')).toBeInTheDocument()

    await act(async () => {
      screen.getByText(/重跑 1 个失败任务/).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(null, true)
    expect(onClose).toHaveBeenCalled()
  })

  it('disables confirm when no failed jobs in failed-node mode', () => {
    renderWithClient(
      <JobRerunDialog
        open
        allowFailedNodeMode
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    expect(screen.getByText(/重跑 0 个失败任务/)).toBeDisabled()
  })

  it('keeps the dialog open and does not call onClose when onConfirm rejects', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('rerun failed'))
    const onClose = vi.fn()
    renderWithClient(
      <JobRerunDialog
        open
        allowFailedNodeMode
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    act(() => {
      screen.getByTestId('rerun-chip-failed-node').click()
    })

    await act(async () => {
      screen.getByText(/重跑 1 个失败任务/).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(null, true)
    expect(onClose).not.toHaveBeenCalled()
    // Dialog stays open with the confirm button re-enabled.
    expect(screen.getByText(/重跑 1 个失败任务/)).toBeEnabled()
  })

  it('does not render failed-node chip when allowFailedNodeMode is false', () => {
    renderWithClient(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(
      screen.queryByTestId('rerun-chip-failed-node')
    ).not.toBeInTheDocument()
  })
})
