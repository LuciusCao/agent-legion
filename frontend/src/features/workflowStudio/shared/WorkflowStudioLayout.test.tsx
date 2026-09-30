import { fireEvent, render, screen, within } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useSettingStore } from '../../../stores/settingStore'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

vi.mock('../chat/StudioChatPanel', () => ({
  StudioChatPanel: (props: Record<string, unknown>) => {
    chatPanelProps(props)
    return <div>chat panel stub</div>
  },
}))

// #804 抽屉化：节点详情抽屉在 Layout 层只验「选中节点即开」，抽屉自身行为
// 见 WorkflowNodeDetailDrawer.test.tsx（那里带齐 api mock）。
vi.mock('../inspector/WorkflowNodeDetailDrawer', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  const ctx = await vi.importActual<typeof import('./studioStateContext')>(
    './studioStateContext'
  )
  return {
    WorkflowNodeDetailDrawer: () => {
      const studio = ctx.useStudioState()
      return studio.selectedNodeKey
        ? React.createElement('div', {
            'data-testid': 'node-detail-drawer',
            'data-node': studio.selectedNodeKey,
          })
        : null
    },
  }
})

// #416：StudioChatAside 轮询 agent 发布请求（react-query）。
vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: vi.fn().mockResolvedValue(null),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}))

const chatPanelProps = vi.fn()

const workflow = {
  key: 'demo_video_workflow',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

const revision = {
  id: 'rev-active',
  workspace_id: 'ws1',
  workflow_key: 'demo_video_workflow',
  version: 1,
  status: 'active',
  definition_hash: '17d8077e',
  created_at: '2026-07-06T10:00:00Z',
  published_at: '2026-07-06T10:05:00Z',
}

const baseProps = {
  loadState: 'ready' as const,
  actionState: 'idle' as const,
  workflow,
  revision,
  activeRevision: revision,
  revisions: [revision],
  executorCatalog: [],
  agentCatalog: [],
  agentCatalogError: false,
  retryAgentCatalog: vi.fn(),
  definitionYaml: 'key: demo_video_workflow\nlabel: 知识视频 DAG\n',
  setDefinitionYaml: vi.fn(),
  selectedNodeKey: null,
  setSelectedNodeKey: vi.fn(),
  validationErrors: [],
  validationMessage: '',
  compareErrors: null,
  compareSummary: null,
  compareState: 'idle' as const,
  dirty: false,
  canSubmit: false,
  canPublish: false,
  createsRevision: true,
  nodes: [],
  edges: [],
  reviewDialogOpen: false,
  closeReviewDialog: vi.fn(),
  changesPanelOpen: false,
  setChangesPanelOpen: vi.fn(),
  yamlEditorOpen: false,
  setYamlEditorOpen: vi.fn(),
  onValidate: vi.fn(),
  onPublish: vi.fn(),
  onReset: vi.fn(),
  publishDraft: vi.fn(),
  viewMode: 'draft' as const,
  selectedRevisionId: revision.id,
  readOnly: false,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

// Layout 不再接收整包 props：studio 经 StudioStateContext 注入，view 字段
// （changesPanelOpen/yamlEditorOpen 等）经 StudioViewContext 注入。
// 伪造对象与真实 StudioState 形状存在字段级差异（null vs 具体对象），
// 走 StudioStateContext 注入，类型上统一放宽为 object。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LayoutStudio = Record<string, any>
function studioProvidersFor(studio: LayoutStudio) {
  // view 专属字段摘出进 StudioViewContext；on* 回调是 AppBar 层的，
  // Layout 子树不再消费。
  const {
    changesPanelOpen,
    setChangesPanelOpen,
    yamlEditorOpen,
    setYamlEditorOpen,
    ...studioState
  } = studio
  const view = makeStudioView({
    ...(changesPanelOpen !== undefined ? { changesPanelOpen } : {}),
    ...(setChangesPanelOpen !== undefined ? { setChangesPanelOpen } : {}),
    ...(yamlEditorOpen !== undefined ? { yamlEditorOpen } : {}),
    ...(setYamlEditorOpen !== undefined ? { setYamlEditorOpen } : {}),
  })
  return { studioState, view }
}

function renderLayout(studio: LayoutStudio) {
  const { studioState, view } = studioProvidersFor(studio)
  // #799：浮动功能岛（挂在 Workspace 内）需要 router 上下文
  // （返回/用量导航的 useNavigate）——TestMemoryRouter 自带 QueryProvider。
  return render(
    <MemoryRouter>
      {withStudioProviders(studioState, view, <WorkflowStudioLayout />)}
    </MemoryRouter>
  )
}

function rerenderLayout(
  rerender: (ui: React.ReactNode) => void,
  studio: LayoutStudio
) {
  const { studioState, view } = studioProvidersFor(studio)
  rerender(
    <MemoryRouter>
      {withStudioProviders(studioState, view, <WorkflowStudioLayout />)}
    </MemoryRouter>
  )
}

describe('WorkflowStudioLayout', () => {
  const localStore = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => localStore.get(key) ?? null,
    setItem: (key: string, value: string) => void localStore.set(key, value),
    removeItem: (key: string) => void localStore.delete(key),
    clear: () => localStore.clear(),
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders mobile panel navigation landmarks', () => {
    renderLayout(baseProps)

    const mobileNav = screen.getByRole('tablist', {
      name: 'Workflow studio panels',
    })
    expect(mobileNav).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: '画布' })
    ).toBeInTheDocument()
    // #804 抽屉化：「编辑节点」页签随分栏退役（节点编辑是全覆盖抽屉）。
    expect(
      within(mobileNav).queryByRole('tab', { name: '编辑节点' })
    ).toBeNull()
    expect(within(mobileNav).getByRole('tab', { name: 'Agent' })).toBeEnabled()
  })

  it('窄屏警示徽标在页签行内、不在画布列里（轮 4 P1-B：Agent 页签整列隐藏也盖不到）', () => {
    renderLayout({
      ...baseProps,
      draftSave: { status: 'error', savedAt: null, conflict: true },
    })
    const badge = screen.getByRole('button', {
      name: '草稿冲突待处理，点击查看',
    })
    expect(
      badge.closest('[data-testid="studio-mobile-nav-row"]')
    ).not.toBeNull()
    // revert 即红：徽标若挂进画布列（或任何 data-mobile-panel 面板），
    // 窄屏切 Agent 页签时被整列 display:none 藏掉。
    expect(badge.closest('[data-mobile-panel]')).toBeNull()
  })

  it('正常保存态不出窄屏警示徽标', () => {
    renderLayout({
      ...baseProps,
      draftSave: { status: 'saved', savedAt: '2026-08-27T09:05:00+00:00' },
    })
    expect(screen.queryByRole('button', { name: /点击查看/ })).toBeNull()
  })

  it('加载/失败态也有返回入口（#799 codex 复核 P2：AppBar 已移除，双岛不挂时最小返回岛常驻）', () => {
    renderLayout({ ...baseProps, loadState: 'loading' as const })
    expect(screen.getByText('正在加载 workflow')).toBeInTheDocument()
    // 返回小岛在加载态可用（双岛不渲染）。
    expect(screen.queryByTestId('studio-identity-island')).toBeNull()
    expect(screen.getByTestId('studio-back-island')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument()
  })

  it('失败态同样挂返回小岛', () => {
    renderLayout({ ...baseProps, loadState: 'error' as const })
    expect(
      screen.getByText('无法加载 active workflow revision')
    ).toBeInTheDocument()
    expect(screen.getByTestId('studio-back-island')).toBeInTheDocument()
  })

  it('renders the empty-state guidance and the workspace editor in empty mode', () => {
    renderLayout({
      ...baseProps,
      loadState: 'empty' as const,
      workflow: null,
      revision: null,
      activeRevision: null,
      revisions: [],
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      '还没有已发布的 workflow'
    )
    // 空态下编辑区照常渲染，用户直接改模板草稿。
    expect(
      screen.getByRole('tablist', { name: 'Workflow studio panels' })
    ).toBeInTheDocument()
  })

  it('dismisses the empty-state guidance persistently per workspace', () => {
    const emptyProps = {
      ...baseProps,
      loadState: 'empty' as const,
      workflow: null,
      revision: null,
      activeRevision: null,
      revisions: [],
    }
    const { rerender } = renderLayout(emptyProps)

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(
      localStorage.getItem('agent-legion:studio-empty-guide-dismissed:ws1')
    ).toBe('1')
    // 重新渲染（如下次进入页面）也不再出现。
    rerenderLayout(rerender, emptyProps)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('asks for confirmation before applying a chat draft over a dirty editor', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderLayout({ ...baseProps, dirty: true })
    const panelProps = chatPanelProps.mock.calls[
      chatPanelProps.mock.calls.length - 1
    ]?.[0] as {
      onApplyWorkflowDraft: (yaml: string) => void
    }

    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))

    expect(confirmSpy).toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))
    expect(baseProps.backToDraft).toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).toHaveBeenCalledWith(
      'key: demo\nlabel: agent\n'
    )
    confirmSpy.mockRestore()
  })

  it('applies a chat draft without confirmation when the editor is clean', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderLayout(baseProps)
    const panelProps = chatPanelProps.mock.calls[
      chatPanelProps.mock.calls.length - 1
    ]?.[0] as {
      onApplyWorkflowDraft: (yaml: string) => void
    }

    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).toHaveBeenCalledWith(
      'key: demo\nlabel: agent\n'
    )
    confirmSpy.mockRestore()
  })

  it('点中节点后开节点详情抽屉（#804 抽屉化：不再切页签/分栏）', () => {
    const { rerender } = renderLayout(baseProps)

    // 未选中：抽屉不开。
    expect(screen.queryByTestId('node-detail-drawer')).toBeNull()
    // 页签停在画布（不再有「编辑节点」页签切换）。
    const mobileNav = screen.getByRole('tablist', {
      name: 'Workflow studio panels',
    })
    expect(
      within(mobileNav).getByRole('tab', { name: '画布' })
    ).toHaveAttribute('aria-selected', 'true')

    rerenderLayout(rerender, { ...baseProps, selectedNodeKey: 'node-a' })

    const drawer = screen.getByTestId('node-detail-drawer')
    expect(drawer).toHaveAttribute('data-node', 'node-a')
    // 页签仍在画布——抽屉是浮层，不切换面板。
    expect(
      within(mobileNav).getByRole('tab', { name: '画布' })
    ).toHaveAttribute('aria-selected', 'true')
  })

  it('opens the changes drawer when changesPanelOpen is set', () => {
    renderLayout({ ...baseProps, changesPanelOpen: true })

    expect(screen.getByText('变更与校验')).toBeInTheDocument()
    expect(screen.getByText('尚未运行校验。')).toBeInTheDocument()
    expect(screen.getByText('变更摘要')).toBeInTheDocument()
  })

  it('opens the full-screen YAML editor dialog when yamlEditorOpen is set', () => {
    renderLayout({ ...baseProps, yamlEditorOpen: true })

    // 工具栏按钮同名，用 dialog 角色定位（aria-labelledby 指向标题）。
    expect(
      screen.getByRole('dialog', { name: '编辑 YAML' })
    ).toBeInTheDocument()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(
      baseProps.definitionYaml
    )
  })
})
