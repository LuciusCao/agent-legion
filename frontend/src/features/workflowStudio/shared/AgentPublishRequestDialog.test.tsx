import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentPublishRequestDialog } from './AgentPublishRequestDialog'
import { makeStudioView, withStudioProviders } from './testStudioProviders'
import { useSettingStore } from '../../../stores/settingStore'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

const mocks = {
  fetchPendingPublishRequest: vi.fn(),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
  fetchWorkflowDraft: vi.fn(),
}

vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: (...args: unknown[]) =>
    mocks.fetchPendingPublishRequest(...args),
  confirmPublishRequest: (...args: unknown[]) =>
    mocks.confirmPublishRequest(...args),
  cancelPublishRequest: (...args: unknown[]) =>
    mocks.cancelPublishRequest(...args),
}))

// #429 三轮 P1-3：confirm 前的重读服务端草稿（对话框传入 flushDraftSave
// 后的审阅-确认一致性链路）。
vi.mock('../../../api/workflowDraft', () => ({
  fetchWorkflowDraft: (...args: unknown[]) => mocks.fetchWorkflowDraft(...args),
}))

const workflow = {
  key: 'demo_video_workflow',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

const revision = {
  id: 'rev-active',
  workspace_id: 'ws1',
  workflow_key: 'demo_video_workflow',
  version: 1,
  status: 'active',
  definition_hash: '17d8077e',
  created_at: '2026-07-06T10:00:00Z',
  published_at: '2026-07-06T10:05:00Z',
}

// 一个有变更的 compare 摘要：确认按钮的 disabled 门（无变更不可发布）。
const summaryWithChange = {
  createsRevision: true,
  riskLevel: 'info' as const,
  severityLabel: '低',
  nodeChanges: [
    {
      type: 'modified' as const,
      nodeKey: 'fetch_items',
      label: '获取题目',
      nodeType: 'code' as const,
      fields: ['label'],
      severity: 'info' as const,
    },
  ],
  edgeChanges: [],
  intakeChanges: [],
  metadataChanges: [],
  riskFlags: [],
  changedNodeKeys: new Set(['fetch_items']),
}

const studioState = {
  loadState: 'ready' as const,
  workflow,
  revision,
  reviewDialogOpen: false,
  closeReviewDialog: vi.fn(),
  publishDraft: vi.fn(),
  createsRevision: true,
  compareSummary: summaryWithChange,
  // #429 三轮 P1-3：确认前 flush 画布草稿保存（对话框经
  // useAgentPublishRequest 传入；flushNow 是 DraftSaveController 的方法）。
  flushDraftSave: vi.fn(),
}

function pendingRecord(): StudioPublishRequestRecord {
  return {
    id: 'req-1',
    workspace_id: 'ws1',
    chat_session_id: 's1',
    status: 'pending',
    created_by: 'studio-agent:u1',
    result_revision_id: null,
    draft_hash: '9f8a'.repeat(16),
    created_at: '2026-09-03T10:00:00Z',
    expires_at: '2026-09-03T10:10:00Z',
    resolved_at: null,
  }
}

function renderDialog(studioOverrides: Record<string, unknown> = {}) {
  return render(
    <TestQueryProvider>
      {withStudioProviders(
        { ...studioState, ...studioOverrides },
        makeStudioView(),
        <AgentPublishRequestDialog />
      )}
    </TestQueryProvider>
  )
}

describe('AgentPublishRequestDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
  })

  it('renders nothing without a pending request', async () => {
    renderDialog()

    await waitFor(() =>
      expect(mocks.fetchPendingPublishRequest).toHaveBeenCalled()
    )
    expect(
      screen.queryByRole('dialog', { name: '发布 workflow revision' })
    ).toBeNull()
  })

  it('opens the shared publish review dialog when a request is pending', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await waitFor(() =>
      expect(
        screen.getByRole('dialog', { name: '发布 workflow revision' })
      ).toBeInTheDocument()
    )
  })

  it('stays closed while the manual review dialog is open', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog({ reviewDialogOpen: true })

    await waitFor(() =>
      expect(mocks.fetchPendingPublishRequest).toHaveBeenCalled()
    )
    expect(
      screen.queryByRole('dialog', { name: '发布 workflow revision' })
    ).toBeNull()
  })

  it('confirm calls the confirm endpoint, not the manual publishDraft action', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: 'key: w\n' })
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
    // 确认走后端确认端点（与手动发布同门禁），不是前端直接 publishDraft。
    expect(studioState.publishDraft).not.toHaveBeenCalled()
  })

  it('confirm flushes the canvas draft save and re-reads the server draft first', async () => {
    // #429 三轮 P1-3 回归钉：画布草稿 800ms debounce 在途时，点确认必须先
    // flush 本页保存（studio.flushDraftSave）再重读服务端草稿，最后才调确
    // 认端点——审阅的 compare summary 与确认发布的 YAML 是同一份（后端
    // hash 校验兜底）。
    const order: string[] = []
    studioState.flushDraftSave = vi.fn(() => order.push('flush'))
    mocks.fetchWorkflowDraft.mockImplementation(async () => {
      order.push('read-draft')
      return { definition_yaml: 'key: w\n' }
    })
    mocks.confirmPublishRequest.mockImplementation(async () => {
      order.push('confirm')
      return {
        ...pendingRecord(),
        status: 'confirmed',
        result_revision_id: 'ws1:demo_video_workflow:v2',
        resolved_at: '2026-09-03T10:02:00Z',
      }
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
    expect(studioState.flushDraftSave).toHaveBeenCalledTimes(1)
    expect(mocks.fetchWorkflowDraft).toHaveBeenCalledWith('ws1')
    expect(order).toEqual(['flush', 'read-draft', 'confirm'])
  })

  it('cancel calls the cancel endpoint and closes the dialog', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    mocks.cancelPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'rejected',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '返回编辑' })
    )

    await waitFor(() =>
      expect(mocks.cancelPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
  })
})
