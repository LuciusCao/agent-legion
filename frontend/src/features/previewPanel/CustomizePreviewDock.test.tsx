/**
 * CustomizePreviewDock 的测试（issue #328 / #795 PR① / #796 验收返工 R2）：
 * Dock = AgentPanelDock + AgentChatPanel（定制预览会话）的纯对话薄组合——
 * 面板内无治理 footer、无 agent 引导文案、无内嵌预览（治理动作与草稿状态
 * 行已迁 PreviewPanelSection 头部，见该文件的测试）；agent 列表缺失时给出
 * 提示；chat 本体由 workflowStudio/chat 自己的测试覆盖，这里 mock 其 API 层。
 * 容器行为（拖拽/折叠/记忆/焦点/非模态）在 agentPanelDock 的测试钉住，
 * 这里只补一条非模态集成断言与「无私有 UI」断言。
 * composer 贴底是 flex 布局契约（AgentChatPanel.chatPanel 高度链 +
 * .messages/.emptyState flex:1），jsdom 测不了布局，由截图验收钉住。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { CustomizePreviewDock } from './CustomizePreviewDock'
import * as chatApi from '../workflowStudio/chat/studioChatApi'
import type { StudioChatSessionRecord } from '../workflowStudio/chat/studioChatApi'
import { EventSourceMock } from '../../testing/eventSourceMock'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../workflowStudio/chat/studioChatApi')
vi.mock('../workflowStudio/chat/studioChatResumeApi')

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

function renderDock() {
  return render(
    (
      <CustomizePreviewDock workspaceId="ws1" onClose={() => undefined} />
    ) as ReactElement,
    { wrapper: TestQueryProvider }
  )
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
})

const originalEventSource = globalThis.EventSource
afterEach(() => {
  globalThis.EventSource = originalEventSource
})

describe('CustomizePreviewDock', () => {
  it('非模态 Dock surface——role=dialog 但 aria-modal=false，底层页面不进 aria-hidden、无 MuiModal 体系', async () => {
    render(
      (
        <div>
          <button type="button">底层左栏按钮</button>
          <CustomizePreviewDock workspaceId="ws1" onClose={() => undefined} />
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

  it('#796 返工 R2：纯对话面板——无治理 footer、无 agent 引导文案、无内嵌预览/iframe', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '定制预览面板' })
    // 治理动作与状态行已迁 PreviewPanelSection 头部：面板内一律不出现。
    expect(screen.queryByRole('button', { name: '预览此草稿' })).toBeNull()
    expect(screen.queryByRole('button', { name: '发布草稿' })).toBeNull()
    expect(screen.queryByRole('button', { name: '恢复默认' })).toBeNull()
    expect(screen.queryByText(/草稿 v1/)).toBeNull()
    // 给 agent 看的引导文案不展示给用户。
    expect(screen.queryByText(/get_preview_guide/)).toBeNull()
    // 无内嵌预览区、不挂任何 iframe（草稿渲染目标只有左栏既有通道）。
    expect(screen.queryByTestId('customize-preview-pane')).toBeNull()
    expect(surface.querySelector('iframe')).toBeNull()
    // 对话骨架在：会话栏 + 空态 + 消息输入（composer）。
    expect(
      await screen.findByText('选择 Agent，点「＋ 新对话」开始')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeInTheDocument()
  })

  it('无可用 agent 时提示配置', async () => {
    renderDock()
    expect(
      await screen.findByText(/未检测到可用的 ACP agent/)
    ).toBeInTheDocument()
  })

  it('#695：busy 时发送进入队列而不是直发撞 409', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'running' }),
    ])
    mockChatApi.sendStudioChatMessage.mockResolvedValue({} as never)
    renderDock()

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

  it('#796 R3：composer chips 在 Dock 里常驻——有会话时权限/模型/思考芯片可见可交互', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({
        capability_snapshot: {
          sessionModes: true,
          sessionConfigOptions: true,
        },
        session_modes: {
          currentModeId: 'default',
          availableModes: [{ id: 'default', name: 'Default' }],
        },
        config_options: [
          {
            id: 'model',
            name: 'Model',
            category: 'model',
            type: 'select',
            currentValue: 'k3',
            options: [{ value: 'k3', name: 'K3' }],
          },
          {
            id: 'thinking',
            name: 'Thinking',
            category: 'thought_level',
            type: 'select',
            currentValue: 'high',
            options: [{ value: 'low' }, { value: 'high' }],
          },
        ],
      } as never),
    ])
    renderDock()

    const surface = await screen.findByRole('dialog', { name: '定制预览面板' })
    // 会话记忆自动恢复最近会话（useStudioChatSessionMemory），chips 在
    // Dock 的 composer 工具行可见且可交互（showAgentConfig 已开启）。
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Agent 权限模式' })
      ).toBeEnabled()
    )
    expect(screen.getByRole('button', { name: '模型' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '思考档位' })).toBeInTheDocument()
    expect(surface.querySelector('iframe')).toBeNull()
  })

  it('#695：closed 会话显示恢复条', async () => {
    mockChatApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi' },
    ] as never)
    mockChatApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'closed', closed_at: '2026-09-01T01:00:00Z' }),
    ])
    renderDock()

    expect(
      await screen.findByRole('button', { name: '继续对话' })
    ).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })
})
