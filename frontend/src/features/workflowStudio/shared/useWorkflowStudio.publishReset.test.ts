import { renderHook, waitFor } from '@testing-library/react'
import { act, createElement, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '../../../testing/testQueryClient'

function queryClientWrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudio } from './useWorkflowStudio'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

/* #1226 四轮 codex P1 回归钉：后端发布成功即删草稿行（#1221），前端发布
 * 成功分支必须把草稿保存控制器同步为「无草稿 + 新 canonical 基线」并让
 * 草稿查询失效重取——否则画布 reset 到 canonical 后，保存控制器的基线仍
 * 是旧草稿文本，schedule 判成「新编辑」→ 800ms debounce 后的尾随 PUT 按
 * CAS-over-absence 语义立刻重建刚删的草稿行，「发布后唯一权威是
 * revision」被架空。两路径（手动 publishDraft / agent confirm）同钉。
 * 时序纪律：debounce 用假时钟推进（shouldAdvanceTime），不等真实 800ms。 */

const activeRevisionPayload = {
  revision: {
    id: 'ws1:demo:v1',
    workspace_id: 'ws1',
    version: 1,
    status: 'active',
    definition_hash: 'hash1234',
    created_at: '2026-07-02T00:00:00Z',
    published_at: '2026-07-02T00:00:00Z',
  },
  workflow: {
    key: 'demo',
    label: 'Demo Workflow',
    intake: { modes: [] },
    nodes: [
      {
        key: 'a',
        label: 'A',
        capability: 'cap_a',
        after: [],
        inputs: [],
        outputs: [],
      },
    ],
    edges: [],
  },
  definition_yaml:
    'key: demo\nlabel: Demo Workflow\nnodes:\n  a:\n    capability: cap_a\n',
}

/* 发布的草稿原文与落库的 canonical 重建（重排 key）——纯文本不等，正是
   尾随 PUT 重建草稿行的触发形态。 */
const EDITED_YAML = 'key: demo\nlabel: My Draft\n'
const CANONICAL_YAML = 'label: My Draft\nkey: demo\n'

const v2Payload = {
  revision: {
    ...activeRevisionPayload.revision,
    id: 'ws1:demo:v2',
    version: 2,
    definition_hash: 'hash5678',
  },
  workflow: activeRevisionPayload.workflow,
  definition_yaml: CANONICAL_YAML,
}

const mocks = {
  fetchActiveWorkflowRevision: vi.fn(),
  fetchWorkflowRevisions: vi.fn(),
  fetchWorkflowRevisionDetail: vi.fn(),
  fetchWorkspaces: vi.fn(),
  compareWorkflowDraft: vi.fn(),
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
  fetchWorkflowDraft: vi.fn(),
  putWorkflowDraft: vi.fn(),
  getAgentCatalog: vi.fn(),
  fetchPendingPublishRequest: vi.fn(),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  fetchActiveWorkflowRevision: (...args: unknown[]) =>
    mocks.fetchActiveWorkflowRevision(...args),
  fetchWorkflowRevisions: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisions(...args),
  fetchWorkflowRevisionDetail: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisionDetail(...args),
  fetchWorkspaces: (...args: unknown[]) => mocks.fetchWorkspaces(...args),
  compareWorkflowDraft: (...args: unknown[]) =>
    mocks.compareWorkflowDraft(...args),
  publishWorkflowDraft: (...args: unknown[]) =>
    mocks.publishWorkflowDraft(...args),
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
  fetchWorkflowDraft: (...args: unknown[]) => mocks.fetchWorkflowDraft(...args),
  putWorkflowDraft: (...args: unknown[]) => mocks.putWorkflowDraft(...args),
}))

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: (...args: unknown[]) => mocks.getAgentCatalog(...args),
}))

vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: (...args: unknown[]) =>
    mocks.fetchPendingPublishRequest(...args),
  confirmPublishRequest: (...args: unknown[]) =>
    mocks.confirmPublishRequest(...args),
  cancelPublishRequest: (...args: unknown[]) =>
    mocks.cancelPublishRequest(...args),
}))

function publishRequestRecord(
  overrides: Partial<StudioPublishRequestRecord> = {}
): StudioPublishRequestRecord {
  return {
    id: 'req-1',
    workspace_id: 'ws1',
    chat_session_id: 's1',
    status: 'pending',
    created_by: 'studio-agent:u1',
    result_revision_id: null,
    draft_hash: null,
    created_at: '2026-09-03T10:00:00Z',
    expires_at: '2026-09-03T10:10:00Z',
    resolved_at: null,
    claimed_at: null,
    ...overrides,
  }
}

/** 装线 + 编辑 + 等过 debounce 让草稿落盘（controller 基线推进到编辑文本）。 */
async function mountWithSavedDraft(result: {
  current: ReturnType<typeof useWorkflowStudio>
}) {
  await waitFor(() => expect(result.current.loadState).toBe('ready'))
  await waitFor(() =>
    expect(result.current.definitionYaml).toBe(
      activeRevisionPayload.definition_yaml
    )
  )
  act(() => {
    result.current.setDefinitionYaml(EDITED_YAML)
  })
  await act(async () => {
    vi.advanceTimersByTime(850)
  })
  expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
  expect(result.current.dirty).toBe(true)
}

function publishSwapsBaselineToCanonicalV2() {
  // 发布成功后的服务端终态：active 换成 canonical 重建的 v2，草稿行已删。
  mocks.fetchActiveWorkflowRevision.mockResolvedValue(v2Payload)
  mocks.fetchWorkflowRevisions.mockResolvedValue({
    revisions: [v2Payload.revision],
  })
  mocks.fetchWorkflowDraft.mockResolvedValue({
    definition_yaml: null,
    updated_at: null,
    generation: 0,
  })
}

async function advancePastDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(2000)
  })
}

describe('useWorkflowStudio publish draft-row cleanup (#1226 R4)', () => {
  beforeEach(() => {
    // 本文件的断言按调用次数计（尾随 PUT 不得发出）——清空上一用例的调用史；
    // 实现随后由 mockResolvedValue 重设。
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.fetchWorkspaces.mockResolvedValue({ workspaces: [{ id: 'ws1' }] })
    mocks.getAgentCatalog.mockResolvedValue({ agents: [] })
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.fetchWorkflowDraft.mockResolvedValue({
      definition_yaml: null,
      updated_at: null,
      generation: 0,
    })
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED_YAML,
      updated_at: '2026-08-27T00:00:00+00:00',
    })
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
    useAgentPublishNoticeStore.setState({
      resolvedNotice: null,
      lastResolvedRequestId: null,
    })
  })

  it('manual publishDraft leaves no trailing PUT recreating the deleted draft row', async () => {
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await mountWithSavedDraft(result)
    publishSwapsBaselineToCanonicalV2()

    await act(async () => {
      await result.current.publishDraft()
    })

    // 画布 reset 到新 canonical 基线、dirty 收敛。
    await waitFor(() =>
      expect(result.current.definitionYaml).toBe(CANONICAL_YAML)
    )
    expect(result.current.dirty).toBe(false)
    // 草稿查询被失效重取（读到已删除的空草稿）。
    await waitFor(() =>
      expect(mocks.fetchWorkflowDraft.mock.calls.length).toBeGreaterThan(1)
    )
    // 关键断言：等过 debounce 窗口，没有尾随 PUT 重建刚删的草稿行。
    const putCalls = mocks.putWorkflowDraft.mock.calls.length
    await advancePastDebounce()
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(putCalls)
    expect(result.current.dirty).toBe(false)
  })

  it('agent publish confirm leaves no trailing PUT either (same cleanup path)', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(publishRequestRecord())
    mocks.confirmPublishRequest.mockResolvedValue(
      publishRequestRecord({
        status: 'confirmed',
        result_revision_id: 'ws1:demo:v2',
        resolved_at: '2026-09-03T10:02:00Z',
      })
    )
    const { result } = renderHook(
      () => {
        const studio = useWorkflowStudio('ws1')
        const agentRequest = useAgentPublishRequest('ws1')
        return { studio, agentRequest }
      },
      { wrapper: queryClientWrapper }
    )
    await waitFor(() => expect(result.current.studio.loadState).toBe('ready'))
    await waitFor(() =>
      expect(result.current.agentRequest.pendingRequest?.id).toBe('req-1')
    )
    act(() => {
      result.current.studio.setDefinitionYaml(EDITED_YAML)
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
    publishSwapsBaselineToCanonicalV2()

    await act(async () => {
      await result.current.agentRequest.confirm(() =>
        result.current.studio.markDraftPublished(EDITED_YAML)
      )
    })

    await waitFor(() =>
      expect(result.current.studio.definitionYaml).toBe(CANONICAL_YAML)
    )
    expect(result.current.studio.dirty).toBe(false)
    const putCalls = mocks.putWorkflowDraft.mock.calls.length
    await advancePastDebounce()
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(putCalls)
  })
})
