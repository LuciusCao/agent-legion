import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { JobActionBar } from './JobActionBar'
import { makeJob } from '../testing/fixtures'
import type { WorkflowDefinitionRecord } from '../types'

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

describe('JobActionBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('enables rerun/delete and disables package for a queued job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'queued' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    const rerun = screen.getByText('重跑')
    const packageBtn = screen.getByText('打包')
    const deleteBtn = screen.getByText('删除')
    expect(rerun).not.toHaveAttribute('disabled')
    expect(packageBtn).toHaveAttribute('disabled')
    expect(deleteBtn).not.toHaveAttribute('disabled')
  })

  it('disables rerun and package for a running job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'running' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    const rerun = screen.getByText('重跑')
    const packageBtn = screen.getByText('打包')
    const deleteBtn = screen.getByText('删除')
    expect(rerun).toHaveAttribute('disabled')
    expect(packageBtn).toHaveAttribute('disabled')
    expect(deleteBtn).not.toHaveAttribute('disabled')
  })

  it('enables rerun, package and delete for a completed job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'completed' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('重跑')).not.toHaveAttribute('disabled')
    expect(screen.getByText('打包')).not.toHaveAttribute('disabled')
    expect(screen.getByText('删除')).not.toHaveAttribute('disabled')
  })

  it('enables rerun and delete but disables package for a failed job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('重跑')).not.toHaveAttribute('disabled')
    expect(screen.getByText('打包')).toHaveAttribute('disabled')
    expect(screen.getByText('删除')).not.toHaveAttribute('disabled')
  })

  it('enables rerun and delete but disables package for a paused job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'paused' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('重跑')).not.toHaveAttribute('disabled')
    expect(screen.getByText('打包')).toHaveAttribute('disabled')
    expect(screen.getByText('删除')).not.toHaveAttribute('disabled')
  })

  it('calls onPackage for single-job package download', async () => {
    const onPackage = vi.fn()
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'completed' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={onPackage}
        onDelete={vi.fn()}
      />
    )
    await act(async () => {
      screen.getByText('打包').click()
    })
    expect(onPackage).toHaveBeenCalledTimes(1)
  })

  it('clears packed status for selected packed jobs', async () => {
    const onClearPacked = vi.fn()
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'completed', packed: 1 })]}
        workflowDefinition={workflow}
        mode="batch"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onClearPacked={onClearPacked}
        onDelete={vi.fn()}
      />
    )

    await act(async () => {
      screen.getByText('清空打包状态').click()
    })

    expect(onClearPacked).toHaveBeenCalledTimes(1)
  })

  it('calls onDelete for delete navigation', async () => {
    const onDelete = vi.fn()
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={onDelete}
      />
    )
    await act(async () => {
      screen.getByText('删除').click()
    })
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('opens rerun dialog and exposes node keys for batch selection', async () => {
    const onRerun = vi.fn()
    render(
      <JobActionBar
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
          }),
        ]}
        workflowDefinition={workflow}
        workflowNodesByKey={workflowNodesByKey}
        mode="batch"
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={onRerun}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    await act(async () => {
      screen.getByText('重跑').click()
    })
    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()
    expect(screen.getByTestId('rerun-chip-extract')).toHaveTextContent('提取')
    expect(screen.getByTestId('rerun-chip-generate')).toHaveTextContent('生成')
    expect(screen.getByTestId('rerun-chip-review')).toHaveTextContent('审核')
    expect(screen.queryByTestId('rerun-chip-failed-node')).toBeInTheDocument()
  })

  it('disables actions when loading', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'completed' })]}
        workflowDefinition={workflow}
        mode="single"
        loading
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByText('打包')).toHaveAttribute('disabled')
    expect(screen.getByText('删除')).toHaveAttribute('disabled')
  })

  it('disables upgrade workflow button when loading', () => {
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            is_workflow_outdated: true,
          }),
        ]}
        mode="batch"
        loading
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
        onUpgradeWorkflow={vi.fn()}
      />
    )
    expect(screen.getByText('升级 workflow')).toHaveAttribute('disabled')
  })

  it('opens run-to dialog when the run-to button is clicked', async () => {
    const onRunTo = vi.fn()
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        workflowNodesByKey={workflowNodesByKey}
        mode="single"
        onRerun={vi.fn()}
        onRunTo={onRunTo}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    await act(async () => {
      screen.getByText('运行到').click()
    })

    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()
    expect(screen.getByTestId('target-chip-extract')).toBeInTheDocument()
  })

  it('shows the continue button for a target-reached paused job', () => {
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'paused',
            execution_control: {
              paused: true,
              pause_reason: 'target_reached',
              mode: 'until_node',
              target_node_key: 'review',
            },
          }),
        ]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    const continueBtn = screen.getByText('继续完整流程')
    expect(continueBtn).toBeInTheDocument()
    expect(continueBtn).not.toHaveAttribute('disabled')
  })

  it('calls onContinue when the continue button is clicked', async () => {
    const onContinue = vi.fn()
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'paused',
            execution_control: {
              paused: true,
              pause_reason: 'target_reached',
              mode: 'until_node',
              target_node_key: 'review',
            },
          }),
        ]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onContinue={onContinue}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    await act(async () => {
      screen.getByText('继续完整流程').click()
    })

    expect(onContinue).toHaveBeenCalledTimes(1)
  })

  it('hides the continue button when the job is not paused for target_reached', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'paused' })]}
        workflowDefinition={workflow}
        mode="single"
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    expect(screen.queryByText('继续完整流程')).not.toBeInTheDocument()
  })

  it('shows upgrade workflow button in batch mode', () => {
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            is_workflow_outdated: true,
          }),
          makeJob({ id: 'j2', status: 'pending', is_workflow_outdated: true }),
        ]}
        mode="batch"
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
        onUpgradeWorkflow={vi.fn()}
      />
    )
    expect(screen.getByText('升级 workflow')).toBeInTheDocument()
    expect(screen.getByText('升级 workflow')).not.toHaveAttribute('disabled')
  })

  it('disables upgrade workflow button when handler is not provided', () => {
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            is_workflow_outdated: true,
          }),
        ]}
        mode="batch"
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('升级 workflow')).toHaveAttribute('disabled')
  })

  it('disables upgrade workflow button when no job is upgradeable', () => {
    render(
      <JobActionBar
        jobs={[
          makeJob({ id: 'j1', status: 'running', is_workflow_outdated: true }),
          makeJob({
            id: 'j2',
            status: 'completed',
            is_workflow_outdated: false,
          }),
        ]}
        mode="batch"
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText('升级 workflow')).toHaveAttribute('disabled')
  })

  it('opens upgrade dialog and forwards upgradeable job ids', async () => {
    const onUpgradeWorkflow = vi.fn().mockResolvedValue(undefined)
    render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'completed',
            is_workflow_outdated: true,
          }),
          makeJob({ id: 'j2', status: 'running', is_workflow_outdated: true }),
        ]}
        mode="batch"
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={vi.fn()}
        onPackage={vi.fn()}
        onDelete={vi.fn()}
        onUpgradeWorkflow={onUpgradeWorkflow}
      />
    )
    await act(async () => {
      screen.getByText('升级 workflow').click()
    })
    expect(screen.getByText('确认升级 workflow')).toBeInTheDocument()
    await act(async () => {
      screen.getByText('升级 1 个任务').click()
    })
    expect(onUpgradeWorkflow).toHaveBeenCalledWith(['j1'])
  })
})
