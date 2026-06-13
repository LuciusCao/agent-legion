import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { JobRerunDialog } from './JobRerunDialog'
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

describe('JobRerunDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nodes in pipeline-definition order', () => {
    const { container } = render(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            pipeline_key: 'question_content',
          }),
        ]}
        pipelineDefinition={pipeline}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()
    const chips = container.querySelectorAll('md-filter-chip')
    expect(chips.length).toBe(3)
    expect(chips[0]?.getAttribute('label')).toBe('提取')
    expect(chips[1]?.getAttribute('label')).toBe('生成')
    expect(chips[2]?.getAttribute('label')).toBe('审核')
  })

  it('shows common node intersection for batch selections across pipelines', () => {
    const { container } = render(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            pipeline_key: 'question_content',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            pipeline_key: 'other_pipeline',
          }),
        ]}
        pipelineDefinition={pipeline}
        pipelineNodesByKey={pipelineNodesByKey}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    const chips = container.querySelectorAll('md-filter-chip')
    expect(chips.length).toBe(1)
    expect(chips[0]?.getAttribute('label')).toBe('提取')
  })

  it('identifies jobs excluded by pipeline mismatch for the selected node', () => {
    const { container } = render(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            pipeline_key: 'question_content',
            source_id: 'Q1',
          }),
          makeJob({
            id: 'j2',
            status: 'completed',
            pipeline_key: 'unknown',
            source_id: 'Q2',
          }),
        ]}
        pipelineDefinition={pipeline}
        pipelineNodesByKey={pipelineNodesByKey}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    const generateChip = container.querySelector('md-filter-chip[label="生成"]')
    expect(generateChip).toBeInTheDocument()
    act(() => {
      ;(generateChip as HTMLElement).click()
    })
    expect(screen.getByText(/以下任务不包含所选节点/)).toBeInTheDocument()
    expect(screen.getByText('Q2')).toBeInTheDocument()
  })

  it('calls onConfirm with the backend-authoritative node key', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    const { container } = render(
      <JobRerunDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            pipeline_key: 'question_content',
          }),
        ]}
        pipelineDefinition={pipeline}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    const reviewChip = container.querySelector('md-filter-chip[label="审核"]')
    act(() => {
      ;(reviewChip as HTMLElement).click()
    })

    await act(async () => {
      screen.getByText(/确认重跑/).click()
    })

    expect(onConfirm).toHaveBeenCalledWith('review')
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when cancel is clicked', () => {
    const onClose = vi.fn()
    render(
      <JobRerunDialog
        open
        jobs={[makeJob({ id: 'j1', status: 'failed' })]}
        pipelineDefinition={pipeline}
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
        pipelineDefinition={pipeline}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })
})
