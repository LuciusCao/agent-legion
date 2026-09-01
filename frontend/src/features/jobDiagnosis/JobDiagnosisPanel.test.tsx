import { createElement, type ReactNode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobDiagnosisPanel } from './JobDiagnosisPanel'
import * as chatApi from '../workflowStudio/chat/studioChatApi'
import '../workflowStudio/chat/studioChatResumeApi'
import * as jobApi from '../../api/jobApi'
import type { StudioChatSessionRecord } from '../workflowStudio/chat/studioChatApi'
import { EventSourceMock } from '../../testing/eventSourceMock'
import { createTestQueryClient } from '../../testing/testQueryClient'

vi.mock('../workflowStudio/chat/studioChatApi')
vi.mock('../workflowStudio/chat/studioChatResumeApi')
vi.mock('../../api/jobApi', () => ({
  rerunJob: vi.fn(),
  runToJob: vi.fn(),
}))

const mockApi = vi.mocked(chatApi)
const mockJobApi = vi.mocked(jobApi)

const TARGET = {
  workspaceId: 'ws1',
  jobId: 'job-1',
  nodeKey: 'write_script',
  nodeLabel: '撰写脚本',
}

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's9',
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

function agentTextMessage(id: string, seq: number, text: string) {
  return {
    id,
    session_id: 's9',
    kind: 'text' as const,
    role: 'agent' as const,
    content: { text },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('JobDiagnosisPanel', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.createStudioChatSession.mockResolvedValue(sessionRecord())
    mockApi.sendStudioChatMessage.mockImplementation((_ws, _session, text) =>
      Promise.resolve({
        id: 'u1',
        session_id: 's9',
        kind: 'text',
        role: 'user',
        content: { text },
        seq: 1,
        created_at: '2026-01-01T00:00:00Z',
      })
    )
    mockJobApi.rerunJob.mockResolvedValue({
      job_id: 'job-1',
      operation: 'rerun',
      status: 'succeeded',
    })
    mockJobApi.runToJob.mockResolvedValue({
      job_id: 'job-1',
      operation: 'run_to',
      status: 'succeeded',
    })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  function renderPanel() {
    return render(<JobDiagnosisPanel workspaceId="ws1" target={TARGET} />, {
      wrapper,
    })
  }

  async function renderReadyPanel() {
    const view = renderPanel()
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalledWith(
        'ws1',
        'kimi'
      )
    )
    await waitFor(() =>
      expect(mockApi.sendStudioChatMessage).toHaveBeenCalled()
    )
    await waitFor(() =>
      expect(EventSourceMock.instances.length).toBeGreaterThan(0)
    )
    return view
  }

  function emit(payload: object) {
    const source =
      EventSourceMock.instances[EventSourceMock.instances.length - 1]
    expect(source).toBeDefined()
    act(() => source!.emitMessage(payload))
  }

  it('auto-creates the session and injects the workspace+job+node context', async () => {
    await renderReadyPanel()
    const primer = mockApi.sendStudioChatMessage.mock.calls[0][2]
    expect(primer).toContain('workspace_id: ws1')
    expect(primer).toContain('job_id: job-1')
    expect(primer).toContain('关注节点: write_script')
    expect(primer).toContain('get_job_context')
  })

  it('creates exactly one session per panel mount', async () => {
    await renderReadyPanel()
    expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1)
    expect(mockApi.sendStudioChatMessage).toHaveBeenCalledTimes(1)
  })

  it('renders the suggested action as a confirm card and executes on confirm', async () => {
    await renderReadyPanel()
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')

    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        [
          '诊断：节点超时。',
          '```json',
          '{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-1", "node_key": "write_script", "reason": "超时重试即可"}}',
          '```',
        ].join('\n')
      ),
    })

    const card = await screen.findByRole('group', {
      name: '建议动作 重跑节点 write_script',
    })
    expect(card).toHaveTextContent('超时重试即可')

    // 执行前不碰动作端点。
    expect(mockJobApi.rerunJob).not.toHaveBeenCalled()
    act(() => {
      screen.getByRole('button', { name: '确认执行' }).click()
    })
    await waitFor(() =>
      expect(mockJobApi.rerunJob).toHaveBeenCalledWith('job-1', 'write_script')
    )
    await waitFor(() => expect(card).toHaveTextContent('已执行'))
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['jobDetail', 'job-1'],
    })
  })

  it('dismisses a suggestion without executing', async () => {
    await renderReadyPanel()
    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        '```json\n{"job_action_suggestion": {"action": "run_to_node", "job_id": "job-1", "node_key": "review_script"}}\n```'
      ),
    })
    await screen.findByRole('group', {
      name: '建议动作 重跑至节点 review_script',
    })
    act(() => {
      screen.getByRole('button', { name: '忽略' }).click()
    })
    expect(mockJobApi.runToJob).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(
        screen.queryByRole('group', {
          name: '建议动作 重跑至节点 review_script',
        })
      ).not.toBeInTheDocument()
    )
  })

  it('ignores suggestions pointing at another job', async () => {
    await renderReadyPanel()
    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        '```json\n{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-OTHER", "node_key": "x"}}\n```'
      ),
    })
    // 等一拍确认没有卡片渲染（findBy 会等到超时，这里用固定帧 + query）。
    await act(async () => {
      await Promise.resolve()
    })
    expect(screen.queryByRole('group', { name: /建议动作/ })).toBeNull()
  })
})
