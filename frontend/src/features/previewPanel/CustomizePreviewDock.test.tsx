/**
 * CustomizePreviewDock 的治理面测试（issue #328 / #347 P1 / #795 PR① /
 * #796 返工）：发布/恢复默认是人工按钮（走 previewPanelApi mutation），
 * 「预览此草稿」是显式动作且仅在有草稿时可点（草稿执行不自动发生——
 * section 层门控与左栏渲染测试见 PreviewPanelSection.test.tsx）；agent
 * 列表缺失时给出提示；chat 本体由 workflowStudio/chat 自己的测试覆盖，
 * 这里 mock 其 API 层。
 * #795 PR①：容器迁为 AgentPanelDock（surface "customize-preview"）——
 * 拖拽/折叠/记忆/焦点等容器行为在 agentPanelDock 自己的测试钉住，这里
 * 只补一条「非模态 surface」的集成断言（底层页面不进 aria-hidden）。
 * #796 验收返工：面板内嵌预览区已撤——面板 = AgentPanelDock +
 * AgentChatPanel + 治理 footer 的薄组合，断言面板内不再出现草稿
 * iframe（草稿渲染目标只有左栏既有通道）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { CustomizePreviewDock } from './CustomizePreviewDock'
import * as previewPanelApi from './previewPanelApi'
import * as chatApi from '../workflowStudio/chat/studioChatApi'
import type { StudioChatSessionRecord } from '../workflowStudio/chat/studioChatApi'
import { EventSourceMock } from '../../testing/eventSourceMock'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('./previewPanelApi')
vi.mock('../workflowStudio/chat/studioChatApi')
vi.mock('../workflowStudio/chat/studioChatResumeApi')

const mockPanelApi = vi.mocked(previewPanelApi)
const mockChatApi = vi.mocked(chatApi)

// 该 jsdom 环境不提供 localStorage：用内存 stub（Dock 容器按 surface key
// 记忆位置/折叠态；同 useStudioChat.test.tsx 的模式）。
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

function renderDock(
  state: previewPanelApi.PreviewPanelState | null,
  previewDraft = false,
  onPreviewDraft: () => void = vi.fn()
) {
  return {
    onPreviewDraft,
    ...render(
      (
        <CustomizePreviewDock
          workspaceId="ws1"
          state={state}
          previewDraft={previewDraft}
          onPreviewDraft={onPreviewDraft}
          onClose={() => undefined}
        />
      ) as ReactElement,
      { wrapper: TestQueryProvider }
    ),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  // Dock 容器按 surface key 记忆位置/折叠态：用例间不互相泄漏。
  localStorageStub.clear()
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

describe('CustomizePreviewDock', () => {
  it('#795 PR①：非模态 Dock surface——role=dialog 但 aria-modal=false，底层页面不进 aria-hidden、无 MuiModal 体系', async () => {
    render(
      (
        <div>
          <button type="button">底层左栏按钮</button>
          <CustomizePreviewDock
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
    // 不走 MUI Modal/ModalManager：没有 Modal 根节点、无遮罩，portal 外的
    // 应用内容不会被打进 aria-hidden（「左栏全程可交互」对读屏器同样成立）。
    expect(document.querySelector('.MuiModal-root')).toBeNull()
    expect(document.querySelector('.MuiBackdrop-root')).toBeNull()
    const underlying = screen.getByRole('button', { name: '底层左栏按钮' })
    expect(underlying.closest('[aria-hidden="true"]')).toBeNull()
  })

  it('#796 返工：面板 = Dock + 对话 + 治理 footer——无内嵌预览区、不挂草稿 iframe', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    // 有草稿且已授权（previewDraft=true）也不在面板内渲染：草稿的渲染
    // 目标只有左栏既有通道（PreviewPanelSection 的 PreviewPanelHost）。
    renderDock({ published: null, draft: makeVersion('draft') }, true)
    await screen.findByRole('dialog', { name: '定制预览面板' })
    expect(screen.queryByTestId('customize-preview-pane')).toBeNull()
    expect(screen.queryByText(/草稿预览（仅本页可见/)).toBeNull()
    const surface = screen.getByRole('dialog', { name: '定制预览面板' })
    expect(surface.querySelector('iframe')).toBeNull()
    // 对话与治理操作都在：会话栏、消息输入、三个 footer 动作。
    expect(
      await screen.findByText('选择 Agent，点「＋ 新对话」开始')
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '预览草稿中' })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发布草稿' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '恢复默认' })).toBeInTheDocument()
  })

  it('无可用 agent 时提示配置', async () => {
    renderDock(null)
    expect(
      await screen.findByText(/未检测到可用的 ACP agent/)
    ).toBeInTheDocument()
  })

  it('无草稿时「预览此草稿」与发布按钮均禁用，有草稿时可点击', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    const { unmount } = renderDock({ published: null, draft: null })
    const previewButton = await screen.findByRole('button', {
      name: '预览此草稿',
    })
    expect(previewButton).toBeDisabled()
    const publishButton = screen.getByRole('button', { name: '发布草稿' })
    expect(publishButton).toBeDisabled()
    unmount()

    const onPreviewDraft = vi.fn()
    renderDock(
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

  it('左栏预览中时按钮显示「预览草稿中」状态', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDock({ published: null, draft: makeVersion('draft') }, true, vi.fn())
    expect(
      await screen.findByRole('button', { name: '预览草稿中' })
    ).toBeInTheDocument()
  })

  it('有草稿时发布按钮可点击并调用发布 API', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDock({ published: null, draft: makeVersion('draft') })
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
    renderDock({ published: makeVersion('published'), draft: null })

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
    renderDock({
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
    renderDock(null)

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
    renderDock(null)

    expect(
      await screen.findByRole('button', { name: '继续对话' })
    ).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })
})
