import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioCanvasIslands } from './StudioCanvasIslands'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

// useParams/useNavigate 的 router 环境在页面测试里才真；这里给岛打桩出
// 固定路由上下文（返回导航断言用）。
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

// useWorkspaceDisplayName 拉 workspace 名：本套件不验名称加载，打桩成固定值。
vi.mock('./useWorkspaceDisplayName', () => ({
  useWorkspaceDisplayName: () => '题目审题',
}))

// 窄屏判定桩（P2-D 窄屏重置出口用例用；matchMedia stub 恒 false 走不了真断点）。
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
  publishing: false,
  validating: false,
  canSubmit: true,
  canPublish: true,
  validationMessage: '',
  selectedRevisionId: null,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  requestPublish: vi.fn(),
  resetDefinition: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

const REVISION = {
  id: 'rev-1',
  workspace_id: 'ws1',
  workflow_key: 'demo',
  version: 1,
  status: 'active',
  definition_hash: 'abcdef1234567890',
  created_at: '2026-07-02T00:00:00Z',
  published_at: '2026-07-02T00:00:00Z',
}

function renderIslands(
  studioOverrides: Record<string, unknown> = {},
  viewOverrides: Record<string, unknown> = {}
) {
  const view = makeStudioView(viewOverrides)
  return render(
    withStudioProviders(
      { ...studioStub, ...studioOverrides },
      view,
      <StudioCanvasIslands />
    )
  )
}

describe('StudioCanvasIslands（#799 双浮岛 + #804 定案重组）', () => {
  it('左上身份岛：返回 + workspace 名（无 modeText）+ 版本选择器 + 状态 chip', () => {
    renderIslands()
    const island = screen.getByTestId('studio-identity-island')
    // #804 定案：标题只剩 workspace 名——「/ 编辑工作流」modeText 与
    // 「基于 v- 的草稿」草稿基线文本均已移除。
    expect(island).toHaveTextContent('题目审题')
    expect(island).not.toHaveTextContent('/ 编辑工作流')
    expect(island).not.toHaveTextContent('基于 v')
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument()
    // 版本选择器紧跟标题右侧；#770：触发键只显示版本号，hash 降级到
    // tooltip / aria-label（只读信息不占岛面）。
    const trigger = within(island).getByRole('button', { name: /版本 v- ·/ })
    expect(trigger).toHaveTextContent(/^v-$/)
    expect(island).not.toHaveTextContent('--------')
    // 干净态（无未发布变更）不显示状态 chip。
    expect(within(island).queryByText('已同步')).toBeNull()
  })

  it('返回按钮导航回 workspace', () => {
    renderIslands()
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1')
  })

  it('左岛生命周期动作（#770 顶栏减法）：只外露发布主按钮；重置收进版本菜单；无校验按钮、无 ⋮ 菜单、无手动保存按钮', () => {
    renderIslands({ dirty: true })
    const island = screen.getByTestId('studio-identity-island')
    expect(
      within(island).getByRole('button', { name: '发布' })
    ).toBeInTheDocument()
    // dirty 时重置也不再外露（低频破坏性动作收进版本菜单）。
    expect(within(island).queryByRole('button', { name: '重置' })).toBeNull()
    // 校验按钮（自动校验取代）、⋮ 溢出菜单、手动「保存草稿」均退役。
    expect(within(island).queryByRole('button', { name: '校验' })).toBeNull()
    expect(
      within(island).queryByRole('button', { name: '更多操作' })
    ).toBeNull()
    expect(
      within(island).queryByRole('button', { name: '保存草稿' })
    ).toBeNull()
    // 分隔线在位（身份/版本族与动作族之间）。
    expect(island.querySelector('[class*="divider"]')).not.toBeNull()
  })

  it('#770：宽屏 dirty 时重置出口在版本菜单（带确认），确认后才重置', () => {
    const resetDefinition = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      renderIslands({ dirty: true, resetDefinition, revisions: [REVISION] })
      fireEvent.click(screen.getByRole('button', { name: /版本 v- ·/ }))
      fireEvent.click(
        screen.getByRole('menuitem', { name: '重置为已发布版本' })
      )
      expect(confirmSpy).toHaveBeenCalledOnce()
      expect(resetDefinition).toHaveBeenCalledOnce()
    } finally {
      confirmSpy.mockRestore()
    }
  })

  it('干净态：版本菜单不出重置项', () => {
    renderIslands({ dirty: false, revisions: [REVISION] })
    fireEvent.click(screen.getByRole('button', { name: /版本 v- ·/ }))
    expect(
      screen.queryByRole('menuitem', { name: '重置为已发布版本' })
    ).toBeNull()
  })

  it('自动校验失败：发布禁用 + tooltip 说明，状态 chip 变红可点击开报告', () => {
    // codex 轮 3 P2 后 canPublish 由 hook 绑定校验结果——岛层拿到的就是
    // canPublish=false；岛负责 tooltip 措辞。
    const setChangesPanelOpen = vi.fn()
    renderIslands(
      { dirty: true, canPublish: false, validationMessage: '校验失败' },
      { setChangesPanelOpen }
    )
    const island = screen.getByTestId('studio-identity-island')
    const publish = within(island).getByRole('button', { name: '发布' })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute(
      'aria-label',
      '校验失败，请修复后重新发布'
    )
    fireEvent.click(within(island).getByText('✗ 校验失败'))
    expect(setChangesPanelOpen).toHaveBeenCalledWith(true)
  })

  it('未校验的脏草稿（hydrate/debounce 窗口）：发布禁用 + 待定案 tooltip', () => {
    renderIslands({ dirty: true, canPublish: false, validationMessage: '' })
    const publish = within(
      screen.getByTestId('studio-identity-island')
    ).getByRole('button', { name: '发布' })
    expect(publish.parentElement).toHaveAttribute(
      'aria-label',
      '草稿校验通过后才能发布'
    )
  })

  it('传输失败（校验服务不可用）与结构失败的发布 tooltip 分措辞（codex 轮 4 P1-1）', () => {
    renderIslands({
      dirty: true,
      canPublish: false,
      validationMessage: '校验失败：network error',
    })
    const publish = within(
      screen.getByTestId('studio-identity-island')
    ).getByRole('button', { name: '发布' })
    expect(publish.parentElement).toHaveAttribute(
      'aria-label',
      '校验服务暂不可用，稍后编辑即自动重试校验'
    )
  })

  it('自动校验通过：绿色 ✓ 校验通过 chip，发布可用', () => {
    renderIslands({ dirty: true, validationMessage: '校验通过' })
    const island = screen.getByTestId('studio-identity-island')
    expect(within(island).getByText('✓ 校验通过')).toBeInTheDocument()
    expect(within(island).getByRole('button', { name: '发布' })).toBeEnabled()
  })

  it('校验进行中：chip 显示 校验中…', () => {
    renderIslands({ dirty: true, validating: true })
    const island = screen.getByTestId('studio-identity-island')
    expect(within(island).getByText('校验中…')).toBeInTheDocument()
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
    // 岛锚进画布列后，窄屏非画布页签（Agent）的隐藏由 [data-mobile-panel] 的
    // display:none 承担（jsdom 无布局验证不了，语义钉在 CanvasPanel 用例
    // 的 mobileActive 类断言）——岛组件自身恒渲染，保证尺寸观察不失效
    // （轮 2 P3：返回 null 会让 ResizeObserver 观察失效）。
    renderIslands({}, { mobilePanel: 'agent' })
    expect(screen.getByTestId('studio-identity-island')).toBeInTheDocument()
    expect(screen.getByTestId('studio-action-island')).toBeInTheDocument()
  })

  it('窄屏画布页签：岛仍在（紧凑形态由 CSS 承担）', () => {
    renderIslands({}, { mobilePanel: 'graph' })
    expect(screen.getByTestId('studio-identity-island')).toBeInTheDocument()
    expect(screen.getByTestId('studio-action-island')).toBeInTheDocument()
  })

  it('抽屉打开时被遮岛加 inert（#812 D4）：宽屏只 inert 被遮的右岛，可见左岛保持可交互（P2-1）', () => {
    // 宽屏 720px 抽屉只遮右岛——persistent 抽屉非模态，左岛（返回/版本/
    // 发布）完全可见，inert 它会误伤可见控件。revert 即红：不分级时左岛
    // 也带 inert。
    const { unmount } = renderIslands()
    expect(screen.getByTestId('studio-identity-island')).not.toHaveAttribute(
      'inert'
    )
    expect(screen.getByTestId('studio-action-island')).not.toHaveAttribute(
      'inert'
    )
    unmount()

    // 宽屏 + 共享素材抽屉打开：只右岛 inert。
    renderIslands({}, { materialsOpen: true })
    expect(screen.getByTestId('studio-identity-island')).not.toHaveAttribute(
      'inert'
    )
    expect(screen.getByTestId('studio-action-island')).toHaveAttribute('inert')
  })

  it('节点详情抽屉打开同样只 inert 右岛（selectedNodeKey 非空，宽屏）', () => {
    renderIslands({ selectedNodeKey: 'node-a' })
    expect(screen.getByTestId('studio-action-island')).toHaveAttribute('inert')
    expect(screen.getByTestId('studio-identity-island')).not.toHaveAttribute(
      'inert'
    )
  })

  it('窄屏抽屉全宽覆盖：双岛一起 inert（P2-1 分级）', () => {
    narrowState.value = true
    try {
      renderIslands({}, { materialsOpen: true })
      expect(screen.getByTestId('studio-identity-island')).toHaveAttribute(
        'inert'
      )
      expect(screen.getByTestId('studio-action-island')).toHaveAttribute(
        'inert'
      )
    } finally {
      narrowState.value = false
    }
  })

  it('轮 4 P2-D：窄屏 dirty 时重置出口收进版本选择器菜单（带确认）', () => {
    const resetDefinition = vi.fn()
    const revision = {
      id: 'rev-1',
      workspace_id: 'ws1',
      workflow_key: 'demo',
      version: 1,
      status: 'active',
      definition_hash: 'abcdef1234567890',
      created_at: '2026-07-02T00:00:00Z',
      published_at: '2026-07-02T00:00:00Z',
    }
    narrowState.value = true
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    try {
      renderIslands({ dirty: true, resetDefinition, revisions: [revision] })
      // 菜单打开后出现「重置为已发布版本」；取消确认不触发重置。
      fireEvent.click(screen.getByRole('button', { name: /版本 v- ·/ }))
      fireEvent.click(
        screen.getByRole('menuitem', { name: '重置为已发布版本' })
      )
      expect(confirmSpy).toHaveBeenCalledOnce()
      expect(resetDefinition).not.toHaveBeenCalled()
    } finally {
      narrowState.value = false
      confirmSpy.mockRestore()
    }
  })

  it('轮 7 P2：空工作区（无 revision）窄屏 dirty 仍有重置出口——触发键不禁用', () => {
    // revisions=[] 时选择器触发按钮原本禁用 → 菜单打不开，窄屏唯一重置
    // 入口失效（宽屏有外露按钮）。有 onResetDraft 时必须保持可点。
    narrowState.value = true
    try {
      renderIslands({ dirty: true, revisions: [] })
      const trigger = screen.getByRole('button', { name: /版本 v- ·/ })
      expect(trigger).toBeEnabled()
      fireEvent.click(trigger)
      expect(
        screen.getByRole('menuitem', { name: '重置为已发布版本' })
      ).toBeInTheDocument()
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
