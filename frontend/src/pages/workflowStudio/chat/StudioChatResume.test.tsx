import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { StudioChatPanel } from './StudioChatPanel'
import * as chatApi from './studioChatApi'
import * as resumeApi from './studioChatResumeApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'

vi.mock('./studioChatApi')
vi.mock('./studioChatResumeApi')

const mockApi = vi.mocked(chatApi)
const mockResume = vi.mocked(resumeApi)

const MEMORY_KEY = 'studio-chat.active-session.ws1'

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写（同
// useStudioRightPanelWidth.test.tsx 的模式）。
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
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <TestQueryProvider>
      <StudioChatPanel onApplyWorkflowDraft={vi.fn()} onSelectNode={vi.fn()} />
    </TestQueryProvider>
  )
}

describe('StudioChatPanel resume', () => {
  const originalEventSource = globalThis.EventSource
  let storage: Storage

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    storage = installLocalStorageStub()
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'closed' }),
    ])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('offers 继续对话 for a closed session and keeps the input disabled', async () => {
    renderPanel()
    expect(
      await screen.findByRole('button', { name: '继续对话' })
    ).toBeInTheDocument()
    expect(screen.getByText(/历史记录已保留/)).toBeInTheDocument()
    const input = screen.getByLabelText('消息输入')
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute(
      'placeholder',
      '会话已关闭或中断，点「继续对话」恢复'
    )
  })

  it('resumes on click and re-enables the input', async () => {
    mockResume.resumeStudioChatSession.mockResolvedValue(
      sessionRecord({ status: 'idle' })
    )
    renderPanel()

    const resumeButton = await screen.findByRole('button', { name: '继续对话' })
    await act(async () => {
      fireEvent.click(resumeButton)
    })
    expect(mockResume.resumeStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    await waitFor(() => expect(screen.getByLabelText('消息输入')).toBeEnabled())
    expect(
      screen.queryByRole('button', { name: '继续对话' })
    ).not.toBeInTheDocument()
  })

  it('surfaces a visible error when resume fails', async () => {
    mockResume.resumeStudioChatSession.mockRejectedValue(new Error('恢复失败'))
    renderPanel()

    const resumeButton = await screen.findByRole('button', { name: '继续对话' })
    await act(async () => {
      fireEvent.click(resumeButton)
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('恢复失败')
    // 失败后可重试，输入保持禁用。
    expect(screen.getByRole('button', { name: '继续对话' })).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })

  it('restores the remembered session for the workspace on re-entry', async () => {
    storage.setItem(MEMORY_KEY, 's2')
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ id: 's1' }),
      sessionRecord({ id: 's2', status: 'closed' }),
    ])
    renderPanel()
    // 记忆优先于「最近会话」回落：恢复选中 s2 而非 sessions[0]。
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith('ws1', 's2')
    )
    expect(storage.getItem(MEMORY_KEY)).toBe('s2')
  })

  it('falls back to the most recent session when the remembered one is gone', async () => {
    storage.setItem(MEMORY_KEY, 'deleted-session')
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ id: 's1' }),
      sessionRecord({ id: 's2', status: 'closed' }),
    ])
    renderPanel()
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith('ws1', 's1')
    )
    // 自动选择同时被记忆，下次重进直接恢复。
    expect(storage.getItem(MEMORY_KEY)).toBe('s1')
  })
})
