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
      nodeType: 'code',
      fields: [],
      severity: 'info',
    },
    {
      type: 'modified',
      nodeKey: 'a',
      label: 'A',
      nodeType: 'code',
      fields: [],
      severity: 'info',
    },
    {
      type: 'removed',
      nodeKey: 'd',
      label: 'D',
      nodeType: 'code',
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
    validating: false,
    validationMessage: '',
    onShowChanges: vi.fn(),
    ...overrides,
  }
  render(<WorkflowStudioStatusChip {...props} />)
  return props
}

describe('WorkflowStudioStatusChip', () => {
  it('renders nothing in the clean state（#804 定案：「已同步」常态不显示）', () => {
    const { container } = render(
      <WorkflowStudioStatusChip
        readOnly={false}
        version={null}
        dirty={false}
        hasPreservedDraft={false}
        summary={null}
        compareState="idle"
        validating={false}
        validationMessage=""
        onShowChanges={vi.fn()}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the viewed revision version when read-only', () => {
    renderChip({ readOnly: true, version: 3 })
    expect(screen.getByText('只读 v3')).toBeInTheDocument()
  })

  it('keeps the draft-changes hint visible on the read-only chip', () => {
    renderChip({
      readOnly: true,
      version: 2,
      summary: makeSummary({ nodeChanges: makeNodeChanges() }),
    })
    const chip = screen.getByText('只读 v2 · 草稿未发布变更 3')
    expect(chip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorWarning')
  })

  it('keeps the read-only version chip while compare is loading', () => {
    renderChip({ readOnly: true, version: 2, compareState: 'loading' })
    expect(screen.getByText('只读 v2')).toBeInTheDocument()
    expect(screen.queryByText('计算中…')).not.toBeInTheDocument()
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
        validating={false}
        validationMessage=""
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

  it('自动校验进行中：校验中… spinner（不可点击）', () => {
    const { container } = render(
      <WorkflowStudioStatusChip
        readOnly={false}
        version={null}
        dirty
        hasPreservedDraft={false}
        summary={null}
        compareState="idle"
        validating
        validationMessage=""
        onShowChanges={vi.fn()}
      />
    )
    expect(screen.getByText('校验中…')).toBeInTheDocument()
    expect(container.querySelector('.MuiCircularProgress-root')).not.toBeNull()
  })

  it('自动校验通过：绿色 ✓ 校验通过，点击开校验报告抽屉', () => {
    const { onShowChanges } = renderChip({
      dirty: true,
      validationMessage: '校验通过',
    })
    const chip = screen.getByText('✓ 校验通过')
    expect(chip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorSuccess')
    fireEvent.click(chip)
    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  it('自动校验失败：红色 ✗ 校验失败，点击开校验报告抽屉', () => {
    const { onShowChanges } = renderChip({
      dirty: true,
      validationMessage: '校验失败',
    })
    const chip = screen.getByText('✗ 校验失败')
    expect(chip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorError')
    fireEvent.click(chip)
    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  it('网络错误也按失败态呈现（校验失败：… 前缀）', () => {
    renderChip({ dirty: true, validationMessage: '校验失败：网络错误' })
    expect(screen.getByText('✗ 校验失败')).toBeInTheDocument()
  })

  it('草稿再编辑后旧校验结果作废：validationMessage 清空回「未发布变更」', () => {
    renderChip({
      dirty: true,
      summary: makeSummary({ nodeChanges: makeNodeChanges() }),
      validationMessage: '',
    })
    expect(screen.getByText('未发布变更 3')).toBeInTheDocument()
    expect(screen.queryByText('✓ 校验通过')).not.toBeInTheDocument()
  })

  it('codex 轮 4 P1-2：校验失败/校验中在窄屏保留紧凑入口（不挂 island secondary 类），其余态继续窄屏隐藏', () => {
    // jsdom 跑不了 @media——钉类名结构：secondary = 窄屏隐藏。失败/校验中
    // 是发布被禁时用户唯一的报告入口，必须窄屏可达（revert：整组挂回
    // secondary 即红）。
    renderChip({ dirty: true, validationMessage: '校验失败' })
    expect(
      screen.getByText('✗ 校验失败').closest('.MuiChip-root')?.className
    ).not.toContain('secondary')
  })

  it('codex 轮 4 P1-2：校验中 chip 同样窄屏可见', () => {
    renderChip({ dirty: true, validating: true })
    expect(
      screen.getByText('校验中…').closest('.MuiChip-root')?.className
    ).not.toContain('secondary')
  })

  it('codex 轮 4 P1-2：通过/未发布变更 chip 窄屏继续隐藏（挂 secondary）', () => {
    const { unmount } = render(
      <WorkflowStudioStatusChip
        readOnly={false}
        version={null}
        dirty
        hasPreservedDraft={false}
        summary={null}
        compareState="idle"
        validating={false}
        validationMessage="校验通过"
        onShowChanges={vi.fn()}
      />
    )
    expect(
      screen.getByText('✓ 校验通过').closest('.MuiChip-root')?.className
    ).toContain('secondary')
    unmount()

    renderChip({
      dirty: true,
      summary: makeSummary({ nodeChanges: makeNodeChanges() }),
    })
    expect(
      screen.getByText('未发布变更 3').closest('.MuiChip-root')?.className
    ).toContain('secondary')
  })

  it('轮 4 P2-E：只读 chip 窄屏保留（不挂 secondary）——窄屏只读态必须有身份提示', () => {
    renderChip({ readOnly: true, version: 3 })
    expect(
      screen.getByText('只读 v3').closest('.MuiChip-root')?.className
    ).not.toContain('secondary')
  })
})
