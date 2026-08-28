import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'

const baseProps = {
  revision: null,
  revisions: [],
  activeRevision: null,
  viewMode: 'draft' as const,
  dirty: true,
  readOnly: false,
  hasPreservedDraft: false,
  compareSummary: null,
  compareState: 'idle' as const,
  actionState: 'idle' as const,
  canSubmit: true,
  canPublish: true,
  selectedRevisionId: null,
  isLoadingRevision: false,
  revisionLoadError: null,
  onSelectRevision: vi.fn(),
  onValidate: vi.fn(),
  onPublish: vi.fn(),
  onReset: vi.fn(),
  onShowChanges: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

describe('WorkflowStudioCommandBar', () => {
  it('renders exactly one status chip and keeps the mode text', () => {
    const { container } = render(<WorkflowStudioCommandBar {...baseProps} />)

    expect(screen.getByText('基于 v- 的草稿')).toBeInTheDocument()
    expect(container.querySelectorAll('.MuiChip-root')).toHaveLength(1)
    expect(screen.getByText('有未发布变更')).toBeInTheDocument()
    // 旧的多 chip（计算变更/风险/已保留当前草稿）不再单独出现。
    expect(screen.queryByText('计算变更')).not.toBeInTheDocument()
    expect(screen.queryByText(/^风险：/)).not.toBeInTheDocument()
    expect(screen.queryByText('已保留当前草稿')).not.toBeInTheDocument()
  })

  it('delegates the status chip click to onShowChanges', () => {
    const onShowChanges = vi.fn()
    render(
      <WorkflowStudioCommandBar {...baseProps} onShowChanges={onShowChanges} />
    )

    fireEvent.click(screen.getByText('有未发布变更'))

    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  it('exposes the draft autosave state as a quiet meta tooltip', () => {
    render(
      <WorkflowStudioCommandBar
        {...baseProps}
        draftSave={{ status: 'error', savedAt: null }}
      />
    )

    expect(screen.getByText('基于 v- 的草稿')).toHaveAttribute(
      'title',
      '草稿自动保存失败（编辑尚未持久化）'
    )
  })

  it('shows the saved-at time in the meta tooltip', () => {
    const savedAt = '2026-08-27T09:05:00+00:00'
    render(
      <WorkflowStudioCommandBar
        {...baseProps}
        draftSave={{ status: 'saved', savedAt }}
      />
    )

    const at = new Date(savedAt)
    const hh = String(at.getHours()).padStart(2, '0')
    const mm = String(at.getMinutes()).padStart(2, '0')
    expect(screen.getByText('基于 v- 的草稿')).toHaveAttribute(
      'title',
      `草稿已保存 ${hh}:${mm}`
    )
  })
})
