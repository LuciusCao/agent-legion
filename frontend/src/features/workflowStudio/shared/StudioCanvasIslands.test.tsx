import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioCanvasIslands } from './StudioCanvasIslands'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

// useParams/useNavigate 的 router 环境在页面测试里才真；这里给岛打桩出
// 固定路由上下文（返回/用量导航断言用）。
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useParams: () => ({ workspaceId: 'ws1' }),
    useNavigate: () => mockNavigate,
  }
})

// 草稿保存控件自带测试，这里打桩掉它的 context 接线，保持岛纯拼装断言。
vi.mock('./WorkflowStudioDraftSaveControl', () => ({
  WorkflowStudioDraftSaveControlContainer: () => null,
}))

// useWorkspaceDisplayName 拉 workspace 名：本套件不验名称，打桩成固定值。
vi.mock('./useWorkspaceDisplayName', () => ({
  useWorkspaceDisplayName: () => '题目审题',
}))

// 窄屏判定打桩（matchMedia stub 恒 false，无法走真实断点）：可变旗标驱动。
const narrowState = { value: false }
vi.mock('./useStudioNarrowViewport', () => ({
  useStudioNarrowViewport: () => narrowState.value,
}))

/** 岛消费的 studio 字段全量空壳（withStudioProviders 的 studio 侧）。 */
const studioStub = {
  revision: null,
  revisions: [],
  activeRevision: null,
  viewMode: 'draft',
  dirty: false,
  readOnly: false,
  hasPreservedDraft: false,
  compareSummary: null,
  compareState: 'idle',
  actionState: 'idle',
  canSubmit: true,
  canPublish: true,
  selectedRevisionId: null,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  requestPublish: vi.fn(),
  resetDefinition: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

function renderIslands(viewOverrides: Record<string, unknown> = {}) {
  const view = makeStudioView(viewOverrides)
  return render(withStudioProviders(studioStub, view, <StudioCanvasIslands />))
}

describe('StudioCanvasIslands（#799：去 AppBar 画布化的双浮岛）', () => {
  it('左上身份岛：返回 + 标题 + 版本状态 + 状态 chip + 版本选择器 + 草稿保存控件', () => {
    renderIslands()
    const island = screen.getByTestId('studio-identity-island')
    expect(island).toHaveTextContent('题目审题 / 编辑工作流')
    expect(island).toHaveTextContent('基于 v- 的草稿')
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument()
  })

  it('返回按钮导航回 workspace', () => {
    renderIslands()
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1')
  })

  it('左岛生命周期动作：校验/发布/重置移入指挥中心岛（#799 重组）', () => {
    renderIslands()
    const island = screen.getByTestId('studio-identity-island')
    expect(island).toHaveTextContent('校验')
    expect(island).toHaveTextContent('发布新版本')
    expect(island).toHaveTextContent('重置')
    // 分隔线在位（身份/版本族与动作族之间）。
    expect(island.querySelector('[class*="divider"]')).not.toBeNull()
  })

  it('右岛收成纯图标组：Agent 开关 + 共享素材，无文字按钮、无用量入口', () => {
    renderIslands()
    const island = screen.getByTestId('studio-action-island')
    expect(
      within(island).getByRole('button', { name: 'toggle agent panel' })
    ).toBeInTheDocument()
    expect(
      within(island).getByRole('button', { name: 'Skill 共享材料' })
    ).toBeInTheDocument()
    // 纯图标组：无文字按钮。
    expect(within(island).queryByRole('button', { name: '校验' })).toBeNull()
    // 用量入口移除（实例级遥测，与 workflow 编辑无语义关系）。
    expect(screen.queryByRole('button', { name: 'Token 使用分析' })).toBeNull()
  })

  it('窄屏非画布页签不渲染岛（不盖编辑器/Agent 面板）；画布页签照常渲染', () => {
    narrowState.value = true
    try {
      renderIslands({ mobilePanel: 'editor' })
      expect(screen.queryByTestId('studio-identity-island')).toBeNull()
      expect(screen.queryByTestId('studio-action-island')).toBeNull()
    } finally {
      narrowState.value = false
    }
  })

  it('窄屏画布页签：岛仍在（紧凑形态由 CSS 承担），顶边让开页签导航', () => {
    narrowState.value = true
    try {
      renderIslands({ mobilePanel: 'graph' })
      expect(screen.getByTestId('studio-identity-island')).toBeInTheDocument()
      expect(screen.getByTestId('studio-action-island')).toBeInTheDocument()
    } finally {
      narrowState.value = false
    }
  })
})
