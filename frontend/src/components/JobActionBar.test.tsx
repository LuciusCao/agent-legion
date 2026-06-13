import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { JobActionBar } from './JobActionBar'
import { makeJob } from '../testing/fixtures'
import type { PipelineDefinitionRecord } from '../types'

const pipeline: PipelineDefinitionRecord = {
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

const pipelineNodesByKey: Record<string, PipelineDefinitionRecord> = {
  question_content: pipeline,
  other_pipeline: {
    key: 'other_pipeline',
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

describe('JobActionBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('enables rerun/delete and disables package for a queued job', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'queued' })]}
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
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

  it('calls onDelete for delete navigation', async () => {
    const onDelete = vi.fn()
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        pipelineDefinition={pipeline}
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
    const { container } = render(
      <JobActionBar
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            pipeline_key: 'question_content',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            pipeline_key: 'question_content',
          }),
        ]}
        pipelineDefinition={pipeline}
        pipelineNodesByKey={pipelineNodesByKey}
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
    const chips = container.querySelectorAll('md-filter-chip')
    expect(chips.length).toBe(3)
    expect(chips[0]?.getAttribute('label')).toBe('提取')
    expect(chips[1]?.getAttribute('label')).toBe('生成')
    expect(chips[2]?.getAttribute('label')).toBe('审核')
  })

  it('disables actions when loading', () => {
    render(
      <JobActionBar
        jobs={[makeJob({ id: 'j1', status: 'completed' })]}
        pipelineDefinition={pipeline}
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
})
