import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { WorkflowStudioStatusChip } from './WorkflowStudioStatusChip'

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

function makeNodeChanges(): ChangeSummaryViewModel['nodeChanges'] {
  return [
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
  ]
}

function renderChip(overrides: Record<string, unknown> = {}) {
  const props = {
    readOnly: false,
    version: null,
    dirty: false,
    hasPreservedDraft: false,
    summary: null,
    compareState: 'idle' as const,
    onShowChanges: vi.fn(),
    ...overrides,
  }
  render(<WorkflowStudioStatusChip {...props} />)
  return props
}

describe('WorkflowStudioStatusChip', () => {
  it('renders a quiet neutral chip when synced', () => {
    const { onShowChanges } = renderChip()
    const chip = screen.getByText('已同步')
    fireEvent.click(chip)
    expect(onShowChanges).not.toHaveBeenCalled()
  })

  it('shows the viewed revision version when read-only', () => {
    renderChip({ readOnly: true, version: 3 })
    expect(screen.getByText('只读 v3')).toBeInTheDocument()
  })

  it('merges the preserved-draft hint into the read-only chip', () => {
    renderChip({ readOnly: true, version: 3, hasPreservedDraft: true })
    const chip = screen.getByText('只读 v3')
    expect(chip.closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('已保留当前草稿')
    )
  })

  it('renders the change count with risk color and breakdown title', () => {
    renderChip({
      summary: makeSummary({
        nodeChanges: makeNodeChanges(),
        riskLevel: 'breaking',
      }),
      dirty: true,
    })
    const chip = screen.getByText('未发布变更 3')
    expect(chip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorError')
    expect(chip.closest('[title]')).toHaveAttribute(
      'title',
      '风险：高 · 新增 1 · 已改 1 · 已删 1 · 将创建新版本'
    )
  })

  it.each([
    ['warning', 'MuiChip-colorWarning'],
    ['info', 'MuiChip-colorInfo'],
  ] as const)('maps risk %s to chip color %s', (riskLevel, colorClass) => {
    renderChip({
      summary: makeSummary({ nodeChanges: makeNodeChanges(), riskLevel }),
      dirty: true,
    })
    expect(
      screen.getByText('未发布变更 3').closest('.MuiChip-root')
    ).toHaveClass(colorClass)
  })

  it('opens the changes panel when the change chip is clicked', () => {
    const { onShowChanges } = renderChip({
      summary: makeSummary({ nodeChanges: makeNodeChanges() }),
      dirty: true,
    })
    fireEvent.click(screen.getByText('未发布变更 3'))
    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  it('shows a spinner inside the same chip while comparing', () => {
    const { container } = render(
      <WorkflowStudioStatusChip
        readOnly={false}
        version={null}
        dirty
        hasPreservedDraft={false}
        summary={null}
        compareState="loading"
        onShowChanges={vi.fn()}
      />
    )
    expect(screen.getByText('计算中…')).toBeInTheDocument()
    expect(container.querySelector('.MuiCircularProgress-root')).not.toBeNull()
  })

  it('falls back to a dirty hint chip when counts are unavailable', () => {
    const { onShowChanges } = renderChip({ dirty: true })
    fireEvent.click(screen.getByText('有未发布变更'))
    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  it('renders the preserved-draft chip with a warning color', () => {
    renderChip({ hasPreservedDraft: true })
    const chip = screen.getByText('已保留当前草稿')
    expect(chip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorWarning')
    expect(chip.closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('已保留当前草稿')
    )
  })
})
