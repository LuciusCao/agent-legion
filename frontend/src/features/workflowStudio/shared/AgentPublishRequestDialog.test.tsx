import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentPublishRequestDialog } from './AgentPublishRequestDialog'
import { makeStudioView, withStudioProviders } from './testStudioProviders'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
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

// #429 四轮 P2-1：toast 经 useUiStore（flush/read-draft 失败中止 confirm）。
vi.mock('../../../stores/uiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../stores/uiStore')>()),
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
  // #429 收尾 P2-1：flush 的 resolve 值携带本次落盘终态
  // （DraftSaveFlushResult）——默认「无内容可发」的 no-op 形态。
  flushDraftSave: vi.fn().mockResolvedValue({ ok: true, state: null }),
  // 顶栏状态文本的展示位（守卫不再读它——React 快照在 await 期间不更新）。
  draftSave: { status: 'saved' as const, savedAt: null },
  // #1122：确认成功后登记发布原文（justPublishedRef 收尾）的接线位；
  // definitionYaml 是服务端草稿缺失时的回落登记值。
  markDraftPublished: vi.fn(),
  definitionYaml: 'key: w\n',
}

function pendingRecord(
  overrides: Partial<StudioPublishRequestRecord> = {}
): StudioPublishRequestRecord {
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
    claimed_at: null,
    ...overrides,
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
    useUiStore.setState({ toast: null })
    useAgentPublishNoticeStore.setState({
      resolvedNotice: null,
      pendingRequestId: null,
      lastResolvedRequestId: null,
    })
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

  // M65 评审 P2-3 钉：镜像 effect 是 pendingRequestId 的唯一写入方——
  // 删掉它，结构卡的「发布请求挂起」引导条会静默消失（引导条测试直接
  // 置 store，不覆盖镜像链路）。
  it('M65：pending 请求经镜像 effect 写入 agentPublishNoticeStore（引导条数据源）', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await waitFor(() =>
      expect(useAgentPublishNoticeStore.getState().pendingRequestId).toBe(
        'req-1'
      )
    )
  })

  it('M65：无 pending 时镜像归 null（陈旧值被清除，引导条撤离）', async () => {
    useAgentPublishNoticeStore.setState({ pendingRequestId: 'stale-req' })
    renderDialog()

    await waitFor(() =>
      expect(mocks.fetchPendingPublishRequest).toHaveBeenCalled()
    )
    await waitFor(() =>
      expect(useAgentPublishNoticeStore.getState().pendingRequestId).toBeNull()
    )
  })

  it('surfaces the read-side skill-contract advisories without blocking', async () => {
    // #542：agent 节点绑定的 skill 无机器契约时，后端在 poll 读取侧现算
    // warnings 并透传——对话框以 advisory 文案呈现，确认按钮仍然可点。
    mocks.fetchPendingPublishRequest.mockResolvedValue(
      pendingRecord({
        warnings: [
          "Node do_thing binds skill 'group/legacy' which declares no machine-readable contract (no contract.yaml); its runtime output validation degrades to existence-only",
        ],
      })
    )
    renderDialog()

    const advisories = await screen.findByTestId('publish-advisories')
    expect(advisories).toHaveTextContent('group/legacy')
    expect(advisories).toHaveTextContent('existence-only')
    expect(
      await screen.findByRole('button', { name: '确认发布' })
    ).toBeEnabled()
  })

  it('renders no advisories block when the payload carries none', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await waitFor(() =>
      expect(
        screen.getByRole('dialog', { name: '发布 workflow revision' })
      ).toBeInTheDocument()
    )
    expect(screen.queryByTestId('publish-advisories')).toBeNull()
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
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
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

  it('confirm awaits the flush PUT before reading the server draft (completion order)', async () => {
    // #429 四轮 P2-1 回归钉：旧实现 flush 不等待（先发优势），flush 的
    // PUT 迟于 confirm 的服务端读落地时会发布旧草稿且 hash 恰好匹配。
    // 现在 read-draft 必须在 flush 的 PUT **resolve 之后**才发出（完成
    // 序），最后才调确认端点。
    const order: string[] = []
    let releasePut: (() => void) | null = null
    studioState.flushDraftSave = vi.fn(
      () =>
        new Promise<{ ok: boolean; state: null }>((resolve) => {
          order.push('flush-put-sent')
          releasePut = () => {
            order.push('flush-put-done')
            resolve({ ok: true, state: null })
          }
        })
    )
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

    // PUT 在途：read-draft 尚未发出（完成序，不是先发优势）。
    await waitFor(() => expect(order).toEqual(['flush-put-sent']))
    expect(mocks.fetchWorkflowDraft).not.toHaveBeenCalled()
    expect(mocks.confirmPublishRequest).not.toHaveBeenCalled()

    const release = releasePut as unknown as () => void
    release()
    await waitFor(() =>
      expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
    expect(studioState.flushDraftSave).toHaveBeenCalledTimes(1)
    expect(mocks.fetchWorkflowDraft).toHaveBeenCalledWith('ws1')
    expect(order).toEqual([
      'flush-put-sent',
      'flush-put-done',
      'read-draft',
      'confirm',
    ])
  })

  it('confirm does not proceed when the server draft re-read fails', async () => {
    // #429 四轮 P2-1：fetchWorkflowDraft reject 时旧实现 confirm 静默不发
    // 生。现在必须提示错误且绝不调确认端点（不发布未确认落盘的内容）。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
    mocks.fetchWorkflowDraft.mockRejectedValue(new Error('draft read failed'))
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('草稿尚未保存成功')
    )
    expect(mocks.confirmPublishRequest).not.toHaveBeenCalled()
  })

  it('confirm does not proceed when the flush save fails', async () => {
    // #429 终局 P2-2（真实失败语义）：flush 的保存链失败不 reject——
    // DraftSaveController.flushNow/save 全路径 resolve，失败态只进 state
    // （status='error'，见 draftSaveController.ts 的注释契约）。旧的
    // rejecting mock 钉的是生产中不存在的契约（catch 守卫不可达）。现在
    // flush resolve + state.status='error' 必须中止 confirm：否则重读到的
    // 是旧草稿，confirm 会发布用户没审过的版本（hash 只闭合「服务端≠
    // 请求」方向）。这是「点击前已 error」的既有形态（收尾 P2-1 后仍被
    // 覆盖：flush 对 error 态重新调度后仍失败，result.ok=false）。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: false,
      state: { status: 'error', savedAt: null },
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: 'key: w\n' })
    renderDialog({ draftSave: { status: 'error', savedAt: null } })

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('草稿尚未保存成功')
    )
    expect(mocks.fetchWorkflowDraft).not.toHaveBeenCalled()
    expect(mocks.confirmPublishRequest).not.toHaveBeenCalled()
  })

  it('confirm does not proceed when the flush PUT fails DURING the await (live result, not snapshot)', async () => {
    // #429 收尾 P2-1 回归钉：点击那一刻 studio.draftSave.status 还是
    // 'pending'（React useState 快照），本次 flush 的 PUT 在 await 期间
    // 失败——resolve-but-failed（DraftSaveController 全路径 resolve，失败
    // 只进 controller state）。旧守卫读闭包里的快照（pending/saving），
    // error 永远不出现，守卫漏过 → confirm 发布旧草稿。现在守卫读
    // flush 的 resolve 值（live 终态）：ok=false 必须中止 confirm。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: false,
      state: { status: 'error', savedAt: null },
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: 'key: w\n' })
    // 快照链路的起点：点击前状态健康（saved）——不是 error。快照在
    // await 期间不会更新（React setState 不改闭包捕获的对象）。
    renderDialog({ draftSave: { status: 'saved', savedAt: null } })

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('草稿尚未保存成功')
    )
    expect(mocks.fetchWorkflowDraft).not.toHaveBeenCalled()
    expect(mocks.confirmPublishRequest).not.toHaveBeenCalled()
  })

  it('confirm proceeds when the flush save resolves and the state is healthy', async () => {
    // #429 终局 P2-2 的另一半：flush resolve + ok=true——落盘成功，
    // confirm 正常继续（重读草稿 → 确认端点）。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: 'key: w\n' })
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
    expect(useUiStore.getState().toast?.message ?? '').not.toContain(
      '草稿尚未保存成功'
    )
  })

  it('stays open showing the publish in progress while the request is confirming', async () => {
    // #429 四轮 P3-2 回归钉：confirm 在途超过一个轮询周期时，轮询返回
    // status='confirming' 的行——对话框不消失，确认按钮呈进行中（disabled）。
    mocks.fetchPendingPublishRequest.mockResolvedValue(
      pendingRecord({
        status: 'confirming',
        claimed_at: '2026-09-03T10:01:30Z',
      })
    )
    renderDialog()

    const confirmButton = await screen.findByRole('button', {
      name: '确认发布',
    })
    expect(
      screen.getByRole('dialog', { name: '发布 workflow revision' })
    ).toBeInTheDocument()
    // confirming 状态下按钮禁用（发布进行中，不可重复触发）。
    await waitFor(() => expect(confirmButton).toBeDisabled())
  })

  it('confirm registers the re-read server draft as just-published (#1122)', async () => {
    // #1122 根因钉：确认成功后必须把发布原文登记进 justPublishedRef
    // （markDraftPublished），否则紧随的 canonical 基线被 baseline sync
    // 判为外部变更，dirty chip 永真。登记值取重读的服务端草稿——它就是
    // 确认端点将发布的内容（后端 confirm 读同一行，hash 校验兜底）。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
    mocks.fetchWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: w\nlabel: V2\n',
    })
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(studioState.markDraftPublished).toHaveBeenCalledWith(
        'key: w\nlabel: V2\n'
      )
    )
    expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
  })

  it('confirm falls back to the canvas yaml when the server draft row is gone (#1122)', async () => {
    // #1122 的回落形态：重读不到服务端草稿行时登记画布原文（此时 confirm
    // 端点也会 404，登记无害——justPublishedRef 在草稿偏离时自动失效）。
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: null })
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: 'ws1:demo_video_workflow:v2',
      resolved_at: '2026-09-03T10:02:00Z',
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    renderDialog({ definitionYaml: 'key: canvas\n' })

    await userEvent.click(
      await screen.findByRole('button', { name: '确认发布' })
    )

    await waitFor(() =>
      expect(studioState.markDraftPublished).toHaveBeenCalledWith(
        'key: canvas\n'
      )
    )
  })

  it('M49 runtime-only 请求（compare 无条目 + 有 runtime 键）：确认保存可用并调确认端点', async () => {
    // 通道死端回归钉：agent 对 tools/requires_labels-only 草稿发起发布
    // 请求时 compare 摘要为空（M47 起这些字段不进 compare）——旧门禁把
    // 确认永久禁用（用户只剩取消）。现在 runtime 键存在即可确认：标题
    // 「保存节点运行配置」、runtime-only 文案、确认端点照常调用（后端按
    // runtime-only 原地生效 + 追加配置版本，result_revision_id 为 null）。
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    studioState.flushDraftSave = vi.fn().mockResolvedValue({
      ok: true,
      state: { status: 'saved', savedAt: null },
    })
    mocks.fetchWorkflowDraft.mockResolvedValue({ definition_yaml: 'key: w\n' })
    mocks.confirmPublishRequest.mockResolvedValue({
      ...pendingRecord(),
      status: 'confirmed',
      result_revision_id: null,
      resolved_at: '2026-09-03T10:02:00Z',
    })
    renderDialog({
      createsRevision: false,
      compareSummary: {
        createsRevision: false,
        riskLevel: 'none' as const,
        severityLabel: '无风险',
        nodeChanges: [],
        edgeChanges: [],
        intakeChanges: [],
        metadataChanges: [],
        riskFlags: [],
        changedNodeKeys: new Set(),
      },
      runtimeChangedKeys: ['nodes.fetch_items.tools'],
    })

    const confirmButton = await screen.findByRole('button', {
      name: '确认保存',
    })
    expect(
      screen.getByRole('dialog', { name: '保存节点运行配置' })
    ).toBeInTheDocument()
    expect(
      screen.getByText(/确认后立即对新执行生效并追加一条配置版本/)
    ).toBeInTheDocument()
    expect(confirmButton).toBeEnabled()

    await userEvent.click(confirmButton)

    await waitFor(() =>
      expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    )
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

  it('disables the confirm button while a cancel is in flight', async () => {
    // #429 四轮 codex P2 回归钉：cancel 在途（canceling）时确认按钮必须
    // disabled——否则快速再点确认会并发 cancel+confirm，终态由后端竞态
    // 决定，可能同时出现成功回执和失败 toast。
    mocks.fetchPendingPublishRequest.mockResolvedValue(pendingRecord())
    let releaseCancel: (() => void) | null = null
    mocks.cancelPublishRequest.mockImplementation(
      () =>
        new Promise((resolve: (value: unknown) => void) => {
          releaseCancel = () => resolve(undefined)
        })
    )
    renderDialog()

    await userEvent.click(
      await screen.findByRole('button', { name: '返回编辑' })
    )

    const confirmButton = screen.getByRole('button', { name: '确认发布' })
    await waitFor(() => expect(confirmButton).toBeDisabled())
    // cancel 落地后按钮恢复可用。
    await act(async () => {
      releaseCancel?.()
    })
    await waitFor(() => expect(confirmButton).toBeEnabled())
  })
})
