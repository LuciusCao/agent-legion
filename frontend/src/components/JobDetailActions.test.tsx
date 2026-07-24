import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { JobDetailActions } from './JobDetailActions'
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
  ],
}

function renderActions(
  props: Partial<React.ComponentProps<typeof JobDetailActions>> = {}
) {
  return render(
    <JobDetailActions
      jobs={[
        makeJob({ id: 'j1', status: 'failed', workflow_key: workflow.key }),
      ]}
      workflowDefinition={workflow}
      onRerun={vi.fn()}
      onPackage={vi.fn()}
      onDelete={vi.fn()}
      onOpenArtifacts={vi.fn()}
      onUpgradeWorkflow={vi.fn()}
      {...props}
    />
  )
}

describe('JobDetailActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders all icon buttons', () => {
    renderActions()
    expect(screen.getByLabelText('重跑')).toBeInTheDocument()
    expect(screen.getByLabelText('运行到')).toBeInTheDocument()
    expect(screen.getByLabelText('打包')).toBeInTheDocument()
    expect(screen.getByLabelText('删除')).toBeInTheDocument()
    expect(screen.getByLabelText('产物文件')).toBeInTheDocument()
  })

  it('clears packed status only for a packed job', async () => {
    const onClearPacked = vi.fn()
    renderActions({
      jobs: [makeJob({ id: 'j1', status: 'completed', packed: 1 })],
      onClearPacked,
    })

    await act(async () => {
      screen.getByLabelText('清空打包状态').click()
    })

    expect(onClearPacked).toHaveBeenCalledTimes(1)
  })

  it('disables clearing packed status for an unpacked job', () => {
    renderActions({
      jobs: [makeJob({ id: 'j1', status: 'completed', packed: 0 })],
      onClearPacked: vi.fn(),
    })

    expect(screen.getByLabelText('清空打包状态')).toHaveAttribute('disabled')
  })

  it('calls onUpgradeWorkflow for an outdated job', async () => {
    const onUpgradeWorkflow = vi.fn()
    renderActions({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'completed',
          is_workflow_outdated: true,
          workflow_version: 1,
          current_workflow_revision_version: 2,
        }),
      ],
      onUpgradeWorkflow,
    })

    await act(async () => {
      screen.getByLabelText('升级 workflow').click()
    })

    expect(onUpgradeWorkflow).toHaveBeenCalledTimes(1)
  })

  it('disables rerun and package for a running job', () => {
    renderActions({ jobs: [makeJob({ id: 'j1', status: 'running' })] })
    expect(screen.getByLabelText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('打包')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('删除')).not.toHaveAttribute('disabled')
  })

  it('enables rerun, package and delete for a completed job', () => {
    renderActions({ jobs: [makeJob({ id: 'j1', status: 'completed' })] })
    expect(screen.getByLabelText('重跑')).not.toHaveAttribute('disabled')
    expect(screen.getByLabelText('打包')).not.toHaveAttribute('disabled')
    expect(screen.getByLabelText('删除')).not.toHaveAttribute('disabled')
  })

  it('disables all actions when loading', () => {
    renderActions({ loading: true })
    expect(screen.getByLabelText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('运行到')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('打包')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('删除')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('产物文件')).toHaveAttribute('disabled')
  })

  it('calls onPackage when package button is clicked', async () => {
    const onPackage = vi.fn()
    renderActions({
      jobs: [makeJob({ id: 'j1', status: 'completed' })],
      onPackage,
    })
    await act(async () => {
      screen.getByLabelText('打包').click()
    })
    expect(onPackage).toHaveBeenCalledTimes(1)
  })

  it('calls onDelete only after confirm in delete dialog', async () => {
    const onDelete = vi.fn()
    renderActions({ onDelete })
    await act(async () => {
      screen.getByLabelText('删除').click()
    })
    expect(screen.getByText(/确定删除任务/)).toBeInTheDocument()
    expect(onDelete).not.toHaveBeenCalled()
    await act(async () => {
      screen.getByText('删除').click()
    })
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('calls onOpenArtifacts when artifact button is clicked', async () => {
    const onOpenArtifacts = vi.fn()
    renderActions({ onOpenArtifacts })
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })
    expect(onOpenArtifacts).toHaveBeenCalledTimes(1)
  })

  it('opens rerun dialog and calls onRerun with selected node', async () => {
    const onRerun = vi.fn()
    renderActions({ onRerun })
    await act(async () => {
      screen.getByLabelText('重跑').click()
    })
    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()
    await act(async () => {
      screen.getByText('确认重跑').click()
    })
    expect(onRerun).toHaveBeenCalledWith('extract', false)
  })

  it('opens run-to dialog and calls onRunTo with selected target', async () => {
    const onRunTo = vi.fn()
    renderActions({ onRunTo })
    await act(async () => {
      screen.getByLabelText('运行到').click()
    })
    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()
    await act(async () => {
      screen.getByText('确认运行到').click()
    })
    expect(onRunTo).toHaveBeenCalledWith('extract', undefined)
  })

  it('shows continue button for target_reached paused job', () => {
    renderActions({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'paused',
          execution_control: {
            paused: true,
            pause_reason: 'target_reached',
            mode: 'until_node',
            target_node_key: 'generate',
          },
        }),
      ],
      onContinue: vi.fn(),
    })
    expect(screen.getByLabelText('继续完整流程')).toBeInTheDocument()
  })
})
