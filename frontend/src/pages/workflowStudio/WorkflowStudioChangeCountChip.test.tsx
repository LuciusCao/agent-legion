import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { WorkflowStudioChangeCountChip } from './WorkflowStudioChangeCountChip'

function makeSummary(
  overrides: Partial<ChangeSummaryViewModel> = {}
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [],
    edgeChanges: [],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(),
    ...overrides,
  }
}

describe('WorkflowStudioChangeCountChip', () => {
  it('renders the unpublished change count with a breakdown title', () => {
    render(
      <WorkflowStudioChangeCountChip
        summary={makeSummary({
          nodeChanges: [
            {
              type: 'added',
              nodeKey: 'c',
              label: 'C',
              nodeType: 'node',
              fields: [],
              severity: 'info',
            },
            {
              type: 'modified',
              nodeKey: 'a',
              label: 'A',
              nodeType: 'node',
              fields: [],
              severity: 'info',
            },
            {
              type: 'removed',
              nodeKey: 'd',
              label: 'D',
              nodeType: 'node',
              fields: [],
              severity: 'warning',
            },
          ],
        })}
      />
    )
    const chip = screen.getByText('未发布变更 3')
    expect(chip).toBeInTheDocument()
    expect(chip.closest('[title]')).toHaveAttribute(
      'title',
      '新增 1 · 已改 1 · 已删 1 · 将创建新版本'
    )
  })

  it('renders nothing without a summary or without node changes', () => {
    const { container } = render(
      <WorkflowStudioChangeCountChip summary={null} />
    )
    expect(container).toBeEmptyDOMElement()
    const { container: empty } = render(
      <WorkflowStudioChangeCountChip summary={makeSummary()} />
    )
    expect(empty).toBeEmptyDOMElement()
  })
})
