import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'

// 草稿保存状态/按钮已迁到 WorkflowStudioDraftSaveControl（自带测试），这里
// 打桩掉它的 context 接线，保持 CommandBar 纯 props 渲染。
vi.mock('./WorkflowStudioDraftSaveControl', () => ({
  WorkflowStudioDraftSaveControlContainer: () => null,
}))

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
})
