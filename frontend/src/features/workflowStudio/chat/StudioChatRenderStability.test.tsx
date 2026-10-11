import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatPanel } from './StudioChatPanel'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'

// #1120（codex P1）渲染计数：流式 chunk 到达时，未触动消息的 MessageItem
// 不得重渲染。计数打在叶子组件（TextBubble/ToolCallCard/Permission）上——
// MessageItem 的 memo 命中时叶子不会被调用，计数增长即 memo 被穿透
// （典型来源：answerPermission/onToggleAllowAll 每 render 新建）。
const counters = vi.hoisted(() => ({
  text: new Map<string, number>(),
  tool: new Map<string, number>(),
  permission: new Map<string, number>(),
}))

function bump(map: Map<string, number>, key: string) {
  map.set(key, (map.get(key) ?? 0) + 1)
}

vi.mock('./studioChatApi')
vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))
vi.mock('./StudioChatTextBubble', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./StudioChatTextBubble')>()
  const Bubble = mod.StudioChatTextBubble
  return {
    StudioChatTextBubble: (props: Parameters<typeof Bubble>[0]) => {
      bump(counters.text, props.message.id)
      return <Bubble {...props} />
    },
  }
})
vi.mock('./StudioChatToolCallCard', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./StudioChatToolCallCard')>()
  const Card = mod.StudioChatToolCallCard
  return {
    StudioChatToolCallCard: (props: Parameters<typeof Card>[0]) => {
      bump(counters.tool, props.call.toolCallId)
      return <Card {...props} />
    },
  }
})
vi.mock('./StudioChatPermission', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./StudioChatPermission')>()
  const Permission = mod.StudioChatPermission
  return {
    ...mod,
    StudioChatPermission: (props: Parameters<typeof Permission>[0]) => {
      bump(counters.permission, props.permission.requestId)
      return <Permission {...props} />
    },
  }
})

const mockApi = vi.mocked(chatApi)

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'running',
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

function chatMessage(
  id: string,
  seq: number,
  kind: 'text' | 'tool_call' | 'permission',
  role: 'user' | 'agent' | 'system',
  content: Record<string, unknown>
) {
  return {
    id,
    session_id: 's1',
    kind,
    role,
    content,
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('StudioChat 渲染稳定性（#1120）', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    counters.text.clear()
    counters.tool.clear()
    counters.permission.clear()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: { entityType: 'question' },
    })
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'text', 'user', { text: '问题' }),
      chatMessage('m2', 2, 'text', 'agent', { text: '回答前半' }),
      chatMessage('m3', 3, 'tool_call', 'agent', {
        sessionUpdate: 'tool_call',
        toolCallId: 't3',
        title: 'Bash',
        status: 'completed',
      }),
      chatMessage('m4', 4, 'permission', 'agent', {
        request_id: 'r1',
        status: 'pending',
        tool_call: { title: 'Bash' },
        options: [{ optionId: 'o1', name: '允许一次', kind: 'allow_once' }],
      }),
    ])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('流式 chunk 只重渲染被触动的消息行', async () => {
    render(
      <TestQueryProvider>
        <StudioChatPanel onApplyWorkflowDraft={vi.fn()} />
      </TestQueryProvider>
    )
    // 初始挂载落定：四条消息都渲染过、SSE 已建立（Bash 同时是工具卡与
    // 权限卡的标题，用 findAll 取）。
    await screen.findByText('问题')
    await screen.findAllByText('Bash')
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    const before = {
      userText: counters.text.get('m1') ?? 0,
      agentText: counters.text.get('m2') ?? 0,
      tool: counters.tool.get('t3') ?? 0,
      permission: counters.permission.get('r1') ?? 0,
    }
    expect(before.userText).toBeGreaterThan(0)
    expect(before.tool).toBeGreaterThan(0)
    expect(before.permission).toBeGreaterThan(0)

    // 三个流式 chunk 打在 m2 上（session running → m2 是流式目标行）。
    const source = EventSourceMock.instances[0]
    for (const text of [
      '回答前半，接一',
      '回答前半，接一句',
      '回答前半，接一句半',
    ]) {
      act(() => {
        source.emitMessage({
          type: 'message',
          message: {
            id: 'm2',
            session_id: 's1',
            kind: 'text',
            role: 'agent',
            content: { text },
          },
        })
      })
    }

    // 不变量：未触动行的渲染次数零增长；m2 行逐 chunk 重渲染。
    expect(counters.text.get('m1')).toBe(before.userText)
    expect(counters.tool.get('t3')).toBe(before.tool)
    expect(counters.permission.get('r1')).toBe(before.permission)
    expect(counters.text.get('m2')!).toBeGreaterThan(before.agentText)
  })
})
