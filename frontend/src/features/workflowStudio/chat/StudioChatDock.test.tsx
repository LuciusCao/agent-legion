import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatDock } from './StudioChatDock'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import { useAgentPublishNoticeStore } from '../shared/agentPublishNoticeStore'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

const mocks = {
  fetchPendingPublishRequest: vi.fn(),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}

vi.mock('./StudioChatPanel', () => ({
  // 带本地 state 的 stub（#797 codex P1 的「隐藏不卸载」断言要观察到
  // 子树 state 存活）：输入框值即 composer 未发送文本的等价物。
  StudioChatPanel: function StatefulStub() {
    const [text, setText] = useState('')
    return (
      <input
        data-testid="chat-stub-input"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
    )
  },
}))

vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: (...args: unknown[]) =>
    mocks.fetchPendingPublishRequest(...args),
  confirmPublishRequest: (...args: unknown[]) =>
    mocks.confirmPublishRequest(...args),
  cancelPublishRequest: (...args: unknown[]) =>
    mocks.cancelPublishRequest(...args),
}))

// 该 jsdom 环境不提供 localStorage：用内存 stub（Dock 容器按 surface key
// 记忆位置/折叠态；同 agentPanelDock 测试的模式）。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

const localStorageStub = installLocalStorageStub()

function pendingRecord(): StudioPublishRequestRecord {
  return {
    id: 'req-1',
    workspace_id: 'ws1',
    chat_session_id: 's1',
    status: 'pending',
    created_by: 'studio-agent:u1',
    result_revision_id: null,
    created_at: '2026-09-03T10:00:00Z',
    expires_at: '2026-09-03T10:10:00Z',
    resolved_at: null,
  }
}

const studioState = {
  selectedNodeKey: null,
  definitionYaml: 'key: demo_video_workflow\n',
  dirty: false,
  backToDraft: vi.fn(),
  setDefinitionYaml: vi.fn(),
  setSelectedNodeKey: vi.fn(),
  requestNodeFocus: vi.fn(),
}

function renderDock(hidden = false, viewOverrides?: Record<string, unknown>) {
  return render(
    <TestQueryProvider>
      {withStudioProviders(
        studioState,
        makeStudioView(viewOverrides),
        <StudioChatDock hidden={hidden} />
      )}
    </TestQueryProvider>
  )
}

/** Dock surface（role=dialog + 标题名）。 */
function dockSurface() {
  return screen.getByRole('dialog', { name: 'Agent 助手' })
}

describe('StudioChatDock（#795 PR②：侧栏 → Dock 浮层）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Dock 容器按 surface key 记忆位置/折叠态：用例间不互相泄漏。
    localStorageStub.clear()
    useSettingStore.setState({ workspaceId: 'ws1' })
    useAgentPublishNoticeStore.setState({ resolvedNotice: null })
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
  })

  it('对话内容承载在 AgentPanelDock（非模态 surface，z 900），不再是侧栏 aside', async () => {
    renderDock()

    const surface = await waitFor(() => dockSurface())
    // 非模态契约：aria-modal=false、无 MUI Modal/遮罩（背后 DAG 全程可交互）。
    expect(surface).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.MuiModal-root')).toBeNull()
    // 旧侧栏形态（complementary aside）不再存在；聊天内容在 Dock 内。
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.getByTestId('chat-stub-input')).toBeInTheDocument()
    // 折叠态已随 #795 收尾移除：标题栏只有关闭按钮，无 chip。
    expect(screen.queryByRole('button', { name: '折叠面板' })).toBeNull()
    expect(screen.queryByRole('button', { name: /已折叠/ })).toBeNull()
    // 无记忆时默认几何 = 新默认尺寸（#795 收尾：520×640 → ×1.1 = 572×704）；
    // jsdom 视口 768 高：高度被钳到 768-56-32=680（#797 轮 7 上限封顶语义）。
    const wrapper = surface.parentElement as HTMLElement
    expect(wrapper.style.width).toBe('572px')
    expect(wrapper.style.height).toBe('680px')
  })

  it('Dock 关闭按钮 = 收起（toggleAgent 组合出口，页签同步在 pageView 层）', async () => {
    const toggleAgent = vi.fn()
    renderDock(false, { toggleAgent })
    await waitFor(() => dockSurface())
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(toggleAgent).toHaveBeenCalledTimes(1)
  })

  it('codex P1（#797）：hidden 隐藏不卸载——子树 state（composer 文本/队列）存活，重开原样恢复', async () => {
    const { rerender } = renderDock()
    await waitFor(() => dockSurface())
    const input = screen.getByTestId('chat-stub-input')
    fireEvent.change(input, { target: { value: '未发送文本' } })
    expect(input).toHaveValue('未发送文本')

    // 关闭（hidden=true）：surface 消失但子树保持挂载，state 不丢。
    rerender(
      <TestQueryProvider>
        {withStudioProviders(
          studioState,
          makeStudioView(),
          <StudioChatDock hidden />
        )}
      </TestQueryProvider>
    )
    expect(screen.queryByRole('dialog', { name: 'Agent 助手' })).toBeNull()
    expect(screen.getByTestId('chat-stub-input')).toHaveValue('未发送文本')

    // 重开：原文恢复（revert 修复——卸载重挂——则输入框是新实例、值为空）。
    rerender(
      <TestQueryProvider>
        {withStudioProviders(
          studioState,
          makeStudioView(),
          <StudioChatDock hidden={false} />
        )}
      </TestQueryProvider>
    )
    await waitFor(() => dockSurface())
    expect(screen.getByTestId('chat-stub-input')).toHaveValue('未发送文本')
  })

  it('复审批次 P3：跨 workspace 切换经 key={workspaceId} 重挂聊天子树——composer 未发送文本不带入新 workspace', async () => {
    // studio 路由参数变化复用组件；key 重挂清空子树本地 state（revert：
    // 无 key，ws1 的残留文本带进 ws2，即红）。stub 的输入框值即 composer
    // 未发送文本的等价物（同 codex P1 用例）。
    renderDock()
    await waitFor(() => dockSurface())
    const input = screen.getByTestId('chat-stub-input')
    fireEvent.change(input, { target: { value: 'ws1 未发送' } })
    expect(input).toHaveValue('ws1 未发送')

    act(() => {
      useSettingStore.setState({ workspaceId: 'ws2' })
    })
    await waitFor(() =>
      expect(screen.getByTestId('chat-stub-input')).toHaveValue('')
    )
  })

  it('codex P2（#797 复审轮 6）：窄屏避让移动端页签导航——Dock 顶边从 nav 实测底边开始', async () => {
    // 假页签导航（高 40）：useStudioMobileNavHeight 实测（宽屏 nav
    // display:none → 实测 0 天然不加成，测试直接钉元素高度）。
    const fakeNav = document.createElement('div')
    fakeNav.setAttribute('data-testid', 'studio-mobile-nav')
    fakeNav.getBoundingClientRect = () => ({ height: 40 }) as DOMRect
    document.body.appendChild(fakeNav)
    try {
      renderDock()
      const surface = await waitFor(() => dockSurface())
      // 顶边 = AppBar 兜底 56 + nav 40 + 8 = 104。jsdom transform 读数带
      // 挂载偏移产物（见 agentPanelDock 测试的 jsdomTransform 注释）：nav
      // 高度在挂载后的 effect 才测到（挂载时 extra=0、y=64），offset 冻结
      // 在 -64——最终 transform = 104 + 64 = 168。x = 1024-572-16=436
      //（#795 收尾默认尺寸 520→572），jsdom 首挂加倍读作 872。
      const wrapper = surface.parentElement as HTMLElement
      expect(wrapper.style.transform).toBe('translate(872px,168px)')
    } finally {
      fakeNav.remove()
    }
  })

  it('shows no notice without a resolved request', async () => {
    renderDock()

    await waitFor(() =>
      expect(mocks.fetchPendingPublishRequest).toHaveBeenCalled()
    )
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('renders the shared resolved notice after a confirm (cross-instance)', async () => {
    // 回执来自共享 zustand store：对话框实例（AgentPublishRequestDialog）里
    // 的 confirm/cancel 动作落定的回执，本组件（另一个 hook 实例）直接可读
    // ——#429 复审修复的跨实例 useState 死功能回归钉。
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    renderDock()
    await waitFor(() => dockSurface())

    // 模拟另一实例（对话框）的确认动作：直接着陆共享回执——等价于
    // AgentPublishRequestDialog 的 onConfirm 调用 agentRequest.confirm() 后
    // landNotice 的效果（hook 层 useAgentPublishRequest.test 已覆盖完整路径）。
    act(() => {
      useAgentPublishNoticeStore
        .getState()
        .landNotice(
          '已按 Agent 请求发布（revision ws1:demo_video_workflow:v2）'
        )
    })

    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('已按 Agent 请求发布')
    expect(notice).toHaveTextContent('ws1:demo_video_workflow:v2')
  })

  it('the notice is dismissable', async () => {
    useAgentPublishNoticeStore
      .getState()
      .landNotice('已拒绝 Agent 的发布请求，Agent 可继续修改草稿')
    renderDock()

    const dismiss = await screen.findByRole('button', {
      name: '关闭发布请求回执',
    })
    await userEvent.click(dismiss)

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(useAgentPublishNoticeStore.getState().resolvedNotice).toBeNull()
  })
})
