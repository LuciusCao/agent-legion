import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import type { ReactElement } from 'react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { JobActionBar } from './JobActionBar'
import { makeJob } from '../../testing/fixtures'
import type { WorkflowDefinitionRecord } from '../../types'

// The preview hook is exercised in useBatchRerunPreview.test.tsx; here we
// stub it so the dialog tests control the returned count.
const previewStub = vi.hoisted(() => ({
  data: undefined as
    | { total_count: number; eligible_count: number }
    | undefined,
}))
vi.mock('./useBatchRerunPreview', () => ({
  useBatchRerunPreview: () => ({ data: previewStub.data }),
}))

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
  return render(ui, { wrapper: TestQueryProvider })
}

describe('JobActionBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('enables rerun/delete and disables package for a queued job', () => {
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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

describe('JobActionBar in allMatching selection mode', () => {
  beforeEach(() => {
    previewStub.data = undefined
  })

  function renderAllMatching(onRerun = vi.fn(), onUpgradeWorkflow = vi.fn()) {
    renderWithClient(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={workflow}
        mode="batch"
        selectedCount={10}
        allMatchingCount={10}
        filters={[{ key: 'clear', label: '取消选择', onClick: vi.fn() }]}
        onExitSelectMode={vi.fn()}
        onRerun={onRerun}
        onRunTo={vi.fn()}
        onPackage={vi.fn()}
        onClearPacked={vi.fn()}
        onDelete={vi.fn()}
        onUpgradeWorkflow={onUpgradeWorkflow}
      />
    )
    return { onRerun, onUpgradeWorkflow }
  }

  it('keeps filter-safe actions enabled and disables per-job actions', () => {
    renderAllMatching()

    expect(screen.getByText(/已选择 10 项/)).toBeInTheDocument()
    expect(screen.getByText('删除')).not.toHaveAttribute('disabled')
    expect(screen.getByText('打包')).not.toHaveAttribute('disabled')
    expect(screen.getByText('清空打包状态')).not.toHaveAttribute('disabled')
    expect(screen.getByText('重跑')).not.toHaveAttribute('disabled')
    expect(screen.getByText('升级 workflow')).not.toHaveAttribute('disabled')
    expect(screen.getByText('运行到')).toHaveAttribute('disabled')
  })

  it('opens the all-matching upgrade dialog and confirms without job ids', async () => {
    const { onUpgradeWorkflow } = renderAllMatching()

    await act(async () => {
      screen.getByText('升级 workflow').click()
    })
    expect(
      screen.getByText(/将对符合筛选条件的 10 个 job 执行 workflow/)
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认升级').click()
    })
    // 不带 jobIds：store 在 allMatching 模式下经 selection filter 服务端解析。
    expect(onUpgradeWorkflow).toHaveBeenCalledWith()
  })

  it('opens the all-matching rerun dialog and confirms full scope', async () => {
    const { onRerun } = renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    expect(
      screen.getByText(/将对符合筛选条件的 10 个 job 执行/)
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑').click()
    })
    expect(onRerun).toHaveBeenCalledWith(null, true, undefined, undefined)
  })

  it('confirms a specific failure category in allMatching mode', async () => {
    const { onRerun } = renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    await act(async () => {
      screen.getByTestId('rerun-chip-technical').click()
    })
    await act(async () => {
      screen.getByText('确认重跑').click()
    })
    expect(onRerun).toHaveBeenCalledWith(null, true, undefined, 'technical')
  })

  it('offers node chips and confirms a node rerun without jobIds', async () => {
    const { onRerun } = renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    // 节点组与失败类别组同时展示。
    expect(screen.getByText('从节点重跑')).toBeInTheDocument()
    expect(screen.getByText('按失败类别重跑')).toBeInTheDocument()
    expect(screen.getByTestId('rerun-chip-extract')).toBeInTheDocument()
    expect(screen.getByTestId('rerun-chip-generate')).toBeInTheDocument()

    await act(async () => {
      screen.getByTestId('rerun-chip-generate').click()
    })
    expect(
      screen.getByText(/重跑节点：生成（按筛选条件由服务端解析）/)
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑').click()
    })
    // 不带 jobIds：store 在 allMatching 模式下经 selection filter 服务端解析。
    expect(onRerun).toHaveBeenCalledWith('generate', false)
  })

  it('switching back to a failure category clears the node selection', async () => {
    const { onRerun } = renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    await act(async () => {
      screen.getByTestId('rerun-chip-extract').click()
    })
    await act(async () => {
      screen.getByTestId('rerun-chip-all-failed').click()
    })
    await act(async () => {
      screen.getByText('确认重跑').click()
    })
    expect(onRerun).toHaveBeenCalledWith(null, true, undefined, undefined)
  })

  it('shows the server-side eligible count in summary and confirm label', async () => {
    previewStub.data = { total_count: 10, eligible_count: 4 }
    const { onRerun } = renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    expect(screen.getByText(/将重跑 4 个任务/)).toBeInTheDocument()

    await act(async () => {
      screen.getByTestId('rerun-chip-generate').click()
    })
    expect(
      screen.getByText('将重跑 4 个任务（重跑节点：生成）')
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑（4）').click()
    })
    expect(onRerun).toHaveBeenCalledWith('generate', false)
  })

  it('disables confirm when the preview reports zero eligible jobs', async () => {
    previewStub.data = { total_count: 10, eligible_count: 0 }
    renderAllMatching()

    await act(async () => {
      screen.getByText('重跑').click()
    })
    expect(screen.getByText(/将重跑 0 个任务/)).toBeInTheDocument()
    expect(screen.getByText('确认重跑（0）')).toBeDisabled()
  })
})
