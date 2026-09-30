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

  it('左岛生命周期动作：校验图标按钮 + 发布主按钮 + ⋮ 溢出菜单（重置收进）', () => {
    renderIslands()
    const island = screen.getByTestId('studio-identity-island')
    // 校验收成图标按钮（aria-label + tooltip 承载文案，不占文字位）。
    expect(
      within(island).getByRole('button', { name: '校验' })
    ).toBeInTheDocument()
    // 发布保持 contained 文字主按钮。
    expect(island).toHaveTextContent('发布新版本')
    // 重置收进 ⋮ 溢出菜单（低频破坏性动作）。
    expect(
      within(island).getByRole('button', { name: '更多操作' })
    ).toBeInTheDocument()
    expect(within(island).queryByText('重置')).toBeNull()
    // 分隔线在位（身份/版本族与动作族之间）。
    expect(island.querySelector('[class*="divider"]')).not.toBeNull()
  })

  it('右岛图标+文字并排：Agent 助手 + 共享素材；无文字按钮、无用量入口', () => {
    renderIslands()
    const island = screen.getByTestId('studio-action-island')
    expect(
      within(island).getByRole('button', { name: 'toggle agent panel' })
    ).toHaveTextContent('Agent 助手')
    expect(
      within(island).getByRole('button', { name: 'Skill 共享材料' })
    ).toHaveTextContent('共享素材')
    // 无文字动作按钮（生命周期动作在左岛）、无用量入口。
    expect(within(island).queryByRole('button', { name: '校验' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Token 使用分析' })).toBeNull()
  })

  it('窄屏隐藏语义移到画布列 CSS（codex 轮 2 P2 锚定迁移）：岛恒挂载，不盖编辑器由画布列 display:none 承担', () => {
    // 岛锚进画布列后，窄屏非画布页签的隐藏由 [data-mobile-panel] 的
    // display:none 承担（jsdom 无布局验证不了，语义钉在 CanvasPanel 用例
    // 的 mobileActive 类断言）——岛组件自身恒渲染，保证尺寸观察不失效
    // （轮 2 P3：返回 null 会让 ResizeObserver 观察失效）。
    narrowState.value = true
    try {
      renderIslands({ mobilePanel: 'editor' })
      expect(screen.getByTestId('studio-identity-island')).toBeInTheDocument()
      expect(screen.getByTestId('studio-action-island')).toBeInTheDocument()
    } finally {
      narrowState.value = false
    }
  })

  it('窄屏画布页签：岛仍在（紧凑形态由 CSS 承担）', () => {
    narrowState.value = true
    try {
      renderIslands({ mobilePanel: 'graph' })
      expect(screen.getByTestId('studio-identity-island')).toBeInTheDocument()
      expect(screen.getByTestId('studio-action-island')).toBeInTheDocument()
    } finally {
      narrowState.value = false
    }
  })

  it('宽屏双岛互斥（#804 codex 轮 2 P1）：左岛 max-width = 容器宽 - 右岛实测宽 - 间距', async () => {
    // jsdom 无布局：桩出 offsetParent（容器宽 1000）与右岛实测宽 200，
    // resize 驱动重算 → 左岛 maxWidth = 1000-200-36=764（revert 掉封顶
    // 逻辑：无 maxWidth 内联样式，即红）。
    renderIslands()
    const identity = screen.getByTestId('studio-identity-island')
    const action = screen.getByTestId('studio-action-island')
    const parent = document.createElement('div')
    Object.defineProperty(parent, 'clientWidth', { value: 1000 })
    Object.defineProperty(identity, 'offsetParent', {
      configurable: true,
      value: parent,
    })
    action.getBoundingClientRect = () => ({ width: 200 }) as DOMRect

    fireEvent(window, new Event('resize'))
    await screen.findByTestId('studio-identity-island')
    expect(identity.style.maxWidth).toBe('764px')
  })
})
