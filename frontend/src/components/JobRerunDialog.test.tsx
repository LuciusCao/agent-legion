import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { JobRerunDialog } from './JobRerunDialog'
import { makeJob } from '../testing/fixtures'
import type { WorkflowDefinitionRecord } from '../types'

const workflow: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  intake: { modes: [] },
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

describe('JobRerunDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nodes in workflow-definition order', () => {
    render(
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
    render(
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
    render(
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
    render(
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

  it('calls onClose when cancel is clicked', () => {
    const onClose = vi.fn()
    render(
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
    const { container } = render(
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
})
