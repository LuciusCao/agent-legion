/**
 * CustomizePreviewDialog 的治理面测试（issue #328 / #347 P1 / #615 方向 A）：
 * 发布/恢复默认是人工按钮（走 previewPanelApi mutation），「预览此草稿」是
 * 显式动作且仅在有草稿时可点（草稿执行不自动发生——section 层门控测试见
 * PreviewPanelSection.test.tsx）；agent 列表缺失时给出提示；chat 本体由
 * workflowStudio/chat 自己的测试覆盖，这里 mock 其 API 层。
 * #615 方向 A（推翻 #701 内嵌预览）：面板是非模态覆盖层——无遮罩
 * （hideBackdrop）、不锁底层滚动（disableScrollLock）、不圈禁焦点
 * （disableEnforceFocus），左栏预览区全程可滚动可交互；面板内不再有
 * 草稿 iframe（渲染目标只有左栏既有通道）。可折叠为右下角小条。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { CustomizePreviewDialog } from './CustomizePreviewDialog'
import * as previewPanelApi from './previewPanelApi'
import * as chatApi from '../workflowStudio/chat/studioChatApi'
import type { StudioChatSessionRecord } from '../workflowStudio/chat/studioChatApi'
import { EventSourceMock } from '../../testing/eventSourceMock'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'
import { overlaySurfaceSx } from './customizePreviewOverlaySx'
import { theme } from '../../theme'
import type { Theme } from '@mui/material/styles'

vi.mock('./previewPanelApi')
vi.mock('../workflowStudio/chat/studioChatApi')
vi.mock('../workflowStudio/chat/studioChatResumeApi')

const mockPanelApi = vi.mocked(previewPanelApi)
const mockChatApi = vi.mocked(chatApi)

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: null,
    capability_snapshot: {},
    allow_all_permissions: false,
    compacting: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function makeVersion(
  status: 'draft' | 'published',
  html = '<!doctype html><html><body>x</body></html>'
): previewPanelApi.PreviewPanelVersion {
  return {
    id: `id-${status}`,
    workspace_id: 'ws1',
    entity_key: 'default',
    version: 1,
    status,
    html,
    html_hash: 'hash',
    created_by: status === 'draft' ? 'studio-agent:u1' : 'user:u1',
    change_note: null,
    created_at: '2026-09-01T00:00:00Z',
    published_at: status === 'published' ? '2026-09-01T00:00:00Z' : null,
  }
}

function renderDialog(
  state: previewPanelApi.PreviewPanelState | null,
  previewDraft = false,
  onPreviewDraft: () => void = vi.fn(),
  onClose: () => void = () => undefined
) {
  return {
    onPreviewDraft,
    ...render(
      (
        <CustomizePreviewDialog
          workspaceId="ws1"
          state={state}
          previewDraft={previewDraft}
          onPreviewDraft={onPreviewDraft}
          onClose={onClose}
        />
      ) as ReactElement,
      { wrapper: TestQueryProvider }
    ),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  EventSourceMock.reset()
  globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
  mockChatApi.fetchStudioChatAgents.mockResolvedValue([])
  mockChatApi.fetchStudioChatSessions.mockResolvedValue([])
  mockChatApi.fetchStudioChatMessages.mockResolvedValue([])
  mockPanelApi.publishPreviewPanel.mockResolvedValue(makeVersion('published'))
  mockPanelApi.archivePreviewPanel.mockResolvedValue({
    published: null,
    draft: null,
  })
})

const originalEventSource = globalThis.EventSource
afterEach(() => {
  globalThis.EventSource = originalEventSource
})

describe('CustomizePreviewDialog', () => {
  it('#615 方向 A：非模态覆盖层——无遮罩、不锁底层滚动、不圈禁焦点', async () => {
    renderDialog(null)
    // 对话框本体在（标题栏可见）。
    expect(await screen.findByText('定制预览面板')).toBeInTheDocument()
    // hideBackdrop：不渲染 MUI 遮罩层——左栏预览区无遮挡。
    expect(document.querySelector('.MuiBackdrop-root')).toBeNull()
    // disableScrollLock：body 不被加 overflow:hidden / 滚动补偿 padding。
    expect(document.body.style.overflow).not.toBe('hidden')
    expect(document.body.style.paddingRight).toBe('')
    // 面板内不再有草稿预览区（#701 内嵌预览已撤）。
    expect(screen.queryByTestId('customize-preview-pane')).toBeNull()
    expect(screen.queryByText(/草稿预览（仅本页可见/)).toBeNull()
  })

  it('codex P2-B：真正的非模态 surface——底层页面不进 aria-hidden，无 MuiModal 体系', async () => {
    render(
      (
        <div>
          <button type="button">底层左栏按钮</button>
          <CustomizePreviewDialog
            workspaceId="ws1"
            state={null}
            previewDraft={false}
            onPreviewDraft={() => undefined}
            onClose={() => undefined}
          />
        </div>
      ) as ReactElement,
      { wrapper: TestQueryProvider }
    )
    const surface = await screen.findByRole('dialog', { name: '定制预览面板' })
    // role=dialog 但 aria-modal=false：读屏器知道这是非模态表面。
    expect(surface).toHaveAttribute('aria-modal', 'false')
    // 不走 MUI Modal/ModalManager：没有 Modal 根节点，portal 外的应用内容
    // 不会被打进 aria-hidden（「左栏全程可交互」对读屏器同样成立）。
    expect(document.querySelector('.MuiModal-root')).toBeNull()
    const underlying = screen.getByRole('button', { name: '底层左栏按钮' })
    expect(underlying.closest('[aria-hidden="true"]')).toBeNull()
  })

  it('Escape 关闭（焦点在面板内时，自实现的 keydown——已无 MUI Modal 代劳）', async () => {
    const onClose = vi.fn()
    renderDialog(null, false, vi.fn(), onClose)
    const surface = await screen.findByRole('dialog', { name: '定制预览面板' })
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('折叠为右下角小条后聊天子树保持挂载，点小条恢复面板', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDialog(null)
    expect(await screen.findByText('定制预览面板')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '折叠对话' }))
    const pill = await screen.findByRole('button', {
      name: /定制预览对话（已折叠，点击展开）/,
    })
    // 折叠只是 display:none 隐藏内容（codex P2-A：卸载会丢队列/输入），
    // 聊天子树保持挂载——输入框仍在 DOM 里。
    expect(screen.getByLabelText('消息输入')).toBeInTheDocument()

    fireEvent.click(pill)
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /已折叠，点击展开/ })
      ).toBeNull()
    )
    expect(screen.getByText('定制预览面板')).toBeInTheDocument()
  })

  it('codex P2-A：折叠/展开不丢聊天状态——排队消息与未发送输入原样保留', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'running' }),
    ])
    mockChatApi.sendStudioChatMessage.mockResolvedValue({} as never)
    renderDialog(null)

    const input = await screen.findByLabelText('消息输入')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    // busy 时发送进入队列（不直发撞 409），再留一段未发送输入。
    fireEvent.change(input, { target: { value: '排队消息' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })
    expect(screen.getByText('排队消息')).toBeInTheDocument()
    fireEvent.change(input, { target: { value: '未发送草稿' } })

    // 折叠 → 展开：队列 chip 与 composer 输入都必须原样还在。
    fireEvent.click(screen.getByRole('button', { name: '折叠对话' }))
    const pill = await screen.findByRole('button', {
      name: /已折叠，点击展开/,
    })
    fireEvent.click(pill)
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /已折叠，点击展开/ })
      ).toBeNull()
    )
    expect(screen.getByText('排队消息')).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toHaveValue('未发送草稿')
  })

  it('codex P2-A（焦点）：折叠后焦点落到展开小条，展开后回到面板——键盘可往返', async () => {
    // 焦点移交 effect 驱动 Tooltip/ButtonBase 的状态更新脱离 act（known
    // noise，与 section 测试里 fake-timer 用例同款的声明方式）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    renderDialog(null)
    const collapseButton = await screen.findByRole('button', {
      name: '折叠对话',
    })
    // 键盘用户场景：焦点在「折叠对话」上按 Enter——内容区随即
    // display:none，焦点必须被显式移交，否则丢失在不可见子树里。
    collapseButton.focus()
    expect(document.activeElement).toBe(collapseButton)
    fireEvent.click(collapseButton)

    const pill = await screen.findByRole('button', {
      name: /已折叠，点击展开/,
    })
    await waitFor(() => expect(document.activeElement).toBe(pill))

    fireEvent.click(pill)
    const surface = screen.getByRole('dialog', { name: '定制预览面板' })
    await waitFor(() => expect(document.activeElement).toBe(surface))
  })

  it('codex P2-B：surface 顶边跟随 AppBar 实测底边（CSS 变量下发），首帧回退声明高度', () => {
    // 实测底边（useAppBarBottom → getBoundingClientRect().bottom，含版本
    // 芯片撑高/放大字体场景）经 CSS 变量 --overlay-top-inset 下发；未测量
    // （首帧 0）时回退 --app-bar-height（AppBar 声明 min-height 的单一
    // 声明点）——不再硬编码 56px。
    const sxFn = overlaySurfaceSx(false) as (
      t: Theme
    ) => Record<string, unknown>
    const docked = sxFn(theme)
    expect(docked.top).toBe(
      'var(--overlay-top-inset, var(--app-bar-height, 56px))'
    )
    // 窄屏浮动卡片（bottom 锚定）同样不得上探盖住 AppBar。
    const narrow = docked[theme.breakpoints.down('lg')] as Record<
      string,
      unknown
    >
    expect(String(narrow.maxHeight)).toContain('--overlay-top-inset')
  })

  it('codex P2-B（实测接线）：AppBar 实测底边经 CSS 变量落到 surface', async () => {
    // 模拟被副标题芯片撑高的 AppBar（真实高度超过 min-height 56）。
    const appBar = document.createElement('div')
    appBar.dataset.testid = 'app-bar'
    appBar.getBoundingClientRect = () =>
      ({
        bottom: 87,
        top: 0,
        left: 0,
        right: 1440,
        width: 1440,
        height: 87,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect
    document.body.appendChild(appBar)
    try {
      renderDialog(null)
      const surface = await screen.findByRole('dialog', {
        name: '定制预览面板',
      })
      await waitFor(() =>
        expect(surface.style.getPropertyValue('--overlay-top-inset')).toBe(
          '87px'
        )
      )
    } finally {
      appBar.remove()
    }
  })

  it('codex P2（z-index）：面板让位全局通知/对话层——低于 Toast/DAG 全屏/用量面板/Modal，高于 AppBar', () => {
    // 分层约定（仓库实际值）：页面内容 < AppBar 100 < 本面板 900 <
    // Toast 1000 = DagFullscreenDialog 1000 < TokenUsageDialog 1190/1200 <
    // MUI Modal 1300——面板是页面级非模态 chrome，全局通知（底部 toast）
    // 与任何全局对话框打开时都必须压在它之上（codex comments 4111446577 /
    // 4111642734）。
    const resolve = (collapsed: boolean) => {
      const sx = overlaySurfaceSx(collapsed)
      if (typeof sx !== 'function') throw new Error('sx 应是 theme 函数')
      return sx(theme) as Record<string, unknown>
    }
    for (const collapsed of [false, true]) {
      expect(resolve(collapsed).zIndex).toBe(900)
    }
  })

  it('无可用 agent 时提示配置', async () => {
    renderDialog(null)
    expect(
      await screen.findByText(/未检测到可用的 ACP agent/)
    ).toBeInTheDocument()
  })

  it('无草稿时「预览此草稿」与发布按钮均禁用，有草稿时可点击', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    const { unmount } = renderDialog({ published: null, draft: null })
    const previewButton = await screen.findByRole('button', {
      name: '预览此草稿',
    })
    expect(previewButton).toBeDisabled()
    const publishButton = screen.getByRole('button', { name: '发布草稿' })
    expect(publishButton).toBeDisabled()
    unmount()

    const onPreviewDraft = vi.fn()
    renderDialog(
      { published: null, draft: makeVersion('draft') },
      false,
      onPreviewDraft
    )
    const enabledPreview = await screen.findByRole('button', {
      name: '预览此草稿',
    })
    expect(enabledPreview).toBeEnabled()
    expect(onPreviewDraft).not.toHaveBeenCalled()
    fireEvent.click(enabledPreview)
    expect(onPreviewDraft).toHaveBeenCalledTimes(1)
  })

  it('面板内不渲染草稿 iframe：授权态（previewDraft=true）也不挂（#615 方向 A 单一通道）', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    // 草稿已就绪且父级判定已授权——渲染目标只有左栏，面板内任何情况下
    // 都不挂草稿 iframe（对话框 role 范围内断言，不碰底层页面）。
    renderDialog({ published: null, draft: makeVersion('draft') }, true)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.querySelector('iframe')).toBeNull()
    // 状态栏仍展示草稿元信息（agent 写草稿的反馈不撤）。
    expect(
      await screen.findByText(/草稿 v1（studio-agent:u1）/)
    ).toBeInTheDocument()
  })

  it('左栏预览中时按钮显示「预览草稿中」状态', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDialog(
      { published: null, draft: makeVersion('draft') },
      true,
      vi.fn()
    )
    expect(
      await screen.findByRole('button', { name: '预览草稿中' })
    ).toBeInTheDocument()
  })

  it('有草稿时发布按钮可点击并调用发布 API', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDialog({ published: null, draft: makeVersion('draft') })
    const enabledPublish = await screen.findByRole('button', {
      name: '发布草稿',
    })
    expect(enabledPublish).toBeEnabled()
    fireEvent.click(enabledPublish)
    await waitFor(() =>
      expect(mockPanelApi.publishPreviewPanel).toHaveBeenCalledWith('ws1')
    )
  })

  it('恢复默认需确认，确认后调用归档 API', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderDialog({ published: makeVersion('published'), draft: null })

    const archiveButton = await screen.findByRole('button', {
      name: '恢复默认',
    })
    fireEvent.click(archiveButton)
    await waitFor(() =>
      expect(mockPanelApi.archivePreviewPanel).toHaveBeenCalledWith('ws1')
    )
    confirmSpy.mockRestore()
  })

  it('状态栏展示草稿与已发布版本归属', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDialog({
      published: makeVersion('published'),
      draft: makeVersion('draft'),
    })
    expect(
      await screen.findByText(/草稿 v1（studio-agent:u1）/)
    ).toBeInTheDocument()
    expect(screen.getByText(/已发布 v1/)).toBeInTheDocument()
  })

  it('#695：busy 时发送进入队列而不是直发撞 409', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'running' }),
    ])
    mockChatApi.sendStudioChatMessage.mockResolvedValue({} as never)
    renderDialog(null)

    const input = await screen.findByLabelText('消息输入')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    fireEvent.change(input, { target: { value: '排队消息' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })
    // busy：不直接发送（直发会被后端单 turn 原子认领 409 拒绝），进入队列。
    expect(mockChatApi.sendStudioChatMessage).not.toHaveBeenCalled()
    expect(screen.getAllByText('排队中 1')[0]).toBeInTheDocument()
    expect(screen.getByText('排队消息')).toBeInTheDocument()
  })

  it('#695：closed 会话显示恢复条', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'closed', closed_at: '2026-09-01T01:00:00Z' }),
    ])
    renderDialog(null)

    expect(
      await screen.findByRole('button', { name: '继续对话' })
    ).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })
})
