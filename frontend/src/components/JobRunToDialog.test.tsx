import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { JobRunToDialog } from './JobRunToDialog'
import type { WorkflowDefinitionRecord } from '../types'
import { makeJob } from '../testing/fixtures'

const pipeline: WorkflowDefinitionRecord = {
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

function clickTargetChip(container: HTMLElement, key: string) {
  const chip = container.querySelector(
    `[data-testid="target-chip-${key}"]`
  ) as HTMLElement | null
  if (!chip) throw new Error(`Target chip not found: ${key}`)
  fireEvent.click(chip)
}

function clickStartChip(container: HTMLElement, key: string | null) {
  const testId = key === null ? 'start-chip-auto' : `start-chip-${key}`
  const chip = container.querySelector(
    `[data-testid="${testId}"]`
  ) as HTMLElement | null
  if (!chip) throw new Error(`Start chip not found: ${testId}`)
  fireEvent.click(chip)
}

describe('JobRunToDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits a target-only run-to', async () => {
    const onConfirm = vi.fn()
    const { container } = render(
      <JobRunToDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />
    )

    await act(async () => {
      clickTargetChip(container, 'review')
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    expect(onConfirm).toHaveBeenCalledWith('review', undefined)
  })

  it('submits a rerun-to with a start node', async () => {
    const onConfirm = vi.fn()
    const { container } = render(
      <JobRunToDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />
    )

    await act(async () => {
      clickTargetChip(container, 'review')
    })

    await act(async () => {
      clickStartChip(container, 'generate')
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    expect(onConfirm).toHaveBeenCalledWith('review', 'generate')
  })

  it('explains ancestor closure for the selected target', async () => {
    const { container } = render(
      <JobRunToDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    await act(async () => {
      clickTargetChip(container, 'review')
    })

    expect(screen.getByText(/将运行以下节点/)).toBeInTheDocument()
    expect(
      container.querySelector('[data-testid="target-chip-extract"]')
    ).toBeInTheDocument()
    expect(
      container.querySelector('[data-testid="target-chip-generate"]')
    ).toBeInTheDocument()
  })

  it('rejects a start node outside the target closure before submission', async () => {
    const onConfirm = vi.fn()
    const { container } = render(
      <JobRunToDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />
    )

    await act(async () => {
      clickTargetChip(container, 'generate')
    })

    await act(async () => {
      clickStartChip(container, 'review')
    })

    expect(screen.getByText(/不在目标节点/)).toBeInTheDocument()

    const confirm = screen.getByText('确认运行到')
    expect(confirm).toHaveAttribute('disabled')

    await act(async () => {
      confirm.click()
    })

    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('closes when cancel is clicked', async () => {
    const onClose = vi.fn()
    render(
      <JobRunToDialog
        open
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={vi.fn()}
        onClose={onClose}
      />
    )

    await act(async () => {
      screen.getByText('取消').click()
    })

    expect(onClose).toHaveBeenCalled()
  })

  it('is not rendered when closed', () => {
    render(
      <JobRunToDialog
        open={false}
        jobs={[
          makeJob({
            id: 'j1',
            status: 'failed',
            workflow_key: 'question_content',
          }),
        ]}
        workflowDefinition={pipeline}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.queryByText('选择运行到节点')).not.toBeInTheDocument()
  })
})
