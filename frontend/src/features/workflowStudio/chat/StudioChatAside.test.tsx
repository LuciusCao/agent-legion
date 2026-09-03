import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatAside } from './StudioChatAside'
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
}

function renderAside() {
  return render(
    <TestQueryProvider>
      {withStudioProviders(
        studioState,
        makeStudioView(),
        <StudioChatAside agentOpen asideClass="test-aside" />
      )}
    </TestQueryProvider>
  )
}

describe('StudioChatAside publish-request notice (#429 P2-1)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    useAgentPublishNoticeStore.setState({ resolvedNotice: null })
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
  })

  it('shows no notice without a resolved request', async () => {
    renderAside()

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
    renderAside()
    await waitFor(() =>
      expect(
        screen.getByRole('complementary', { name: 'Agent 对话面板' })
      ).toBeInTheDocument()
    )

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
    renderAside()

    const dismiss = await screen.findByRole('button', {
      name: '关闭发布请求回执',
    })
    await userEvent.click(dismiss)

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(useAgentPublishNoticeStore.getState().resolvedNotice).toBeNull()
  })
})
