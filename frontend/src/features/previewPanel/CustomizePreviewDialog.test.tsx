/**
 * CustomizePreviewDialog 的治理面测试（issue #328 / #347 P1）：发布/恢复默认
 * 是人工按钮（走 previewPanelApi mutation），「预览此草稿」是显式动作且仅
 * 在有草稿时可点（草稿执行不自动发生——section 层门控测试见
 * PreviewPanelSection.test.tsx）；agent 列表缺失时给出提示；chat 本体由
 * workflowStudio/chat 自己的测试覆盖，这里 mock 其 API 层。
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
  status: 'draft' | 'published'
): previewPanelApi.PreviewPanelVersion {
  return {
    id: `id-${status}`,
    workspace_id: 'ws1',
    entity_key: 'default',
    version: 1,
    status,
    html: '<!doctype html><html><body>x</body></html>',
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
  onPreviewDraft: () => void = vi.fn()
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
          onClose={() => undefined}
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
