import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

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

// #668：CommandBar 内嵌的 Agent 面板开关读 StudioViewContext，
// 渲染需挂 providers（studio 状态本套件不消费，给空壳）。
function renderCommandBar(
  props: Record<string, unknown> = {},
  viewOverrides: Record<string, unknown> = {}
) {
  const view = makeStudioView(viewOverrides)
  return {
    view,
    ...render(
      withStudioProviders(
        {},
        view,
        <WorkflowStudioCommandBar {...baseProps} {...props} />
      )
    ),
  }
}

describe('WorkflowStudioCommandBar', () => {
  it('renders exactly one status chip and keeps the mode text', () => {
    const { container } = renderCommandBar()

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
    renderCommandBar({ onShowChanges })

    fireEvent.click(screen.getByText('有未发布变更'))

    expect(onShowChanges).toHaveBeenCalledTimes(1)
  })

  // #668：Agent 面板开关的唯一入口在 appbar（CommandBar actions 区），
  // 开合状态走 StudioViewContext。
  it('renders the agent panel toggle and delegates to view.toggleAgent', () => {
    const { view } = renderCommandBar()

    const toggle = screen.getByRole('button', { name: 'toggle agent panel' })
    expect(toggle).toBeInTheDocument()
    fireEvent.click(toggle)

    expect(view.toggleAgent).toHaveBeenCalledTimes(1)
  })

  it('reflects the agent panel open state in the toggle tooltip', async () => {
    renderCommandBar()

    fireEvent.mouseOver(
      screen.getByRole('button', { name: 'toggle agent panel' })
    )
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      '收起 Agent 面板'
    )
  })

  it('renders the shared-materials entry button in the actions area', () => {
    // #643：入口在 actions 区；未点击时抽屉不挂载、不触发查询。
    renderCommandBar()

    expect(
      screen.getByRole('button', { name: 'Skill 共享材料' })
    ).toBeInTheDocument()
  })
})
