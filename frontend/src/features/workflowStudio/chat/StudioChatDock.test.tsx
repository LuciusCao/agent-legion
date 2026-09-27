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
    // surface key 记忆：折叠一次后写入 studio-chat 键。
    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    await screen.findByRole('button', { name: /已折叠，点击展开/ })
    expect(
      window.localStorage.getItem('agent-panel-dock:studio-chat')
    ).toContain('"collapsed":true')
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
