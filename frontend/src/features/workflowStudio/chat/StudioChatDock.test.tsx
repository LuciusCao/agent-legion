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
  StudioChatPanel: () => <div>chat panel stub</div>,
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

function renderDock() {
  return render(
    <TestQueryProvider>
      {withStudioProviders(studioState, makeStudioView(), <StudioChatDock />)}
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
    expect(screen.getByText('chat panel stub')).toBeInTheDocument()
    // surface key 记忆：折叠一次后写入 studio-chat 键。
    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    await screen.findByRole('button', { name: /已折叠，点击展开/ })
    expect(
      window.localStorage.getItem('agent-panel-dock:studio-chat')
    ).toContain('"collapsed":true')
  })

  it('Dock 关闭按钮 = 收起（appbar 开关同一状态源 toggleAgent）', async () => {
    const toggleAgent = vi.fn()
    render(
      <TestQueryProvider>
        {withStudioProviders(
          studioState,
          makeStudioView({ toggleAgent }),
          <StudioChatDock />
        )}
      </TestQueryProvider>
    )
    await waitFor(() => dockSurface())
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(toggleAgent).toHaveBeenCalledTimes(1)
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
