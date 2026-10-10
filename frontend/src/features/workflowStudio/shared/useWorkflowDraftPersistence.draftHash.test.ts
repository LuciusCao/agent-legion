/* #1143/#1177：草稿语义身份（savedHash）主题的持久化用例
   （自 useWorkflowDraftPersistence.test.ts 按测试文件体积纪律拆出，
   用例零改动迁移；mock/setup 与原文件同构）。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DRAFT_NEVER_SAVED } from '../../../api/workflowDraft'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'

const mocks = {
  fetchWorkflowDraft: vi.fn(),
  putWorkflowDraft: vi.fn(),
}

/* #633/#1143：PUT/GET 响应形状（definition_hash 由服务端语义身份计算；
   旧服务端为 null）。 */
type DraftStoreResponseMock = {
  definition_yaml: string
  updated_at: string
  definition_hash: string | null
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  fetchWorkflowDraft: (...args: unknown[]) => mocks.fetchWorkflowDraft(...args),
  putWorkflowDraft: (...args: unknown[]) => mocks.putWorkflowDraft(...args),
}))
vi.mock('../../../api/workflowDraft', () => ({
  DRAFT_NEVER_SAVED: 'never-saved',
  WorkflowDraftConflictError: class extends Error {
    readonly currentDraft: {
      definition_yaml: string | null
      updated_at: string | null
      definition_hash: string | null
    }
    constructor(detail: unknown) {
      super('workflow draft conflict')
      const payload =
        typeof detail === 'object' && detail !== null
          ? (detail as { current_draft?: unknown })
          : {}
      const current = (payload.current_draft ?? {}) as {
        definition_yaml?: string | null
        updated_at?: string | null
        definition_hash?: string | null
      }
      this.currentDraft = {
        definition_yaml: current.definition_yaml ?? null,
        updated_at: current.updated_at ?? null,
        definition_hash: current.definition_hash ?? null,
      }
    }
  },
}))

const SERVER_DRAFT: DraftStoreResponseMock = {
  definition_yaml: 'key: demo\nlabel: Server\n',
  updated_at: '2026-08-27T01:02:03+00:00',
  definition_hash: null,
}
const NO_DRAFT: {
  definition_yaml: null
  updated_at: null
  definition_hash: null
} = {
  definition_yaml: null,
  updated_at: null,
  definition_hash: null,
}

type HookProps = {
  workspaceId: string | undefined
  draftYaml: string
  originalYaml: string
  serverDraft: DraftStoreResponseMock | typeof NO_DRAFT | undefined
  loadError?: boolean
}

function renderPersistence(initial: HookProps) {
  return renderHook(
    (props: HookProps) =>
      useWorkflowDraftPersistence(
        props.workspaceId,
        props.draftYaml,
        props.originalYaml,
        props.serverDraft,
        props.loadError
      ),
    { initialProps: initial }
  )
}

describe('useWorkflowDraftPersistence 草稿语义身份（#1143/#1177 savedHash）', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  // #1143（方案 B）：服务端草稿的语义身份随 hydrate / PUT 成功进入
  // state.savedHash，聊天草稿卡用它做一致性核对。
  it('exposes the server draft identity hash after hydration', () => {
    const { result } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: SERVER_DRAFT.definition_yaml,
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: { ...SERVER_DRAFT, definition_hash: 'hash-agent-v1' },
    })

    expect(result.current.state.savedHash).toBe('hash-agent-v1')
  })

  // #1177 codex P2：hydrate 的服务端草稿明确无身份（hash=null，不可解析
  // 草稿）必须清除既有 savedHash——「已保存 H → 服务端推进为无身份草稿」
  // 若保留旧 H，stale hint 会拿旧 H 误判「与卡相同」隐藏真实分歧提示。
  it('clears the stale savedHash when the hydrated draft has no identity (hash=null)', () => {
    // 先以带身份 H 的草稿 hydrate（保存链另会在 PUT 成功时推进 H——
    // 这里直接经 serverDraft 首装 hydrate 建立旧值）。
    const first = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: SERVER_DRAFT.definition_yaml,
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: { ...SERVER_DRAFT, definition_hash: 'hash-old' },
    })
    expect(first.result.current.state.savedHash).toBe('hash-old')

    // 服务端草稿推进为不可解析内容（hash=null）：hydrate 显式 null 清除
    // 旧 hash（stale hint 之后按「编辑器无身份」降级字符串比较，不误判）。
    const { result } = renderPersistence({
      workspaceId: 'ws2-null-hash',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: {
        ...SERVER_DRAFT,
        definition_yaml: 'key: demo\nlabel: unparseable]]\n',
        definition_hash: null,
      },
    })
    expect(result.current.state.savedHash).toBeNull()
  })

  it('reports the saved identity hash from the PUT response (#1143)', async () => {
    // 保存响应带回 definition_hash：画布重排后的语义身份，供草稿卡核对。
    mocks.putWorkflowDraft.mockResolvedValue({
      ...SERVER_DRAFT,
      definition_hash: 'hash-after-put',
    })
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })

    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })

    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(result.current.state.savedHash).toBe('hash-after-put')
  })

  it('清空画布不发起 PUT 且离开 settled（#1177 评审 V1：空白 skip 的状态收口）', async () => {
    // 保存成功后清空编辑器：空白永不落盘（服务端拒存），「已保存」的
    // settled 状态不得原样保留——否则 stale hint 拿旧 savedHash 短路，
    // 隐藏「编辑器已空白」的真实分歧。
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))

    rerender({
      workspaceId: 'ws1',
      draftYaml: '',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    expect(result.current.state.status).toBe('pending')
    // 清空后没有任何 PUT（仅之前 Edited 的一次落盘）。
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
  })

  it('debounce 窗口内清空画布：窗口内的内容不落盘（#1177 评审 V2）', async () => {
    // 输入 E 后在 800ms 窗口内全选删除：已 arm 的保存必须撤销——
    // 「清空即放弃」，E 不得照旧到期落盘。
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Abandoned\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: '',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()

    // 重新输入照常调度落盘。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Retyped\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Retyped\n',
      {
        expectedUpdatedAt: DRAFT_NEVER_SAVED,
      }
    )
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
  })

  it('作废的成功响应同样推进 savedHash（#1177 评审 V6）——revert 后身份恰好正确', async () => {
    let resolvePut: (value: DraftStoreResponseMock) => void = () => {}
    mocks.putWorkflowDraft.mockImplementationOnce(
      () =>
        new Promise<DraftStoreResponseMock>((resolve) => {
          resolvePut = resolve
        })
    )
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    // E1 落盘在途 → 用户继续输入 E2（pending 窗口）。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: E1\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: E1\n',
      {
        expectedUpdatedAt: DRAFT_NEVER_SAVED,
      }
    )
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: E2\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    // E1 的响应在 E2 调度后到达：requestId 过期作废，但基线与语义身份
    // 都是服务端真值——savedHash 一并推进（修复前停留 null/旧值）。
    await act(async () => {
      resolvePut({
        definition_yaml: 'key: demo\nlabel: E1\n',
        updated_at: '2026-08-27T02:00:00+00:00',
        definition_hash: 'hash-e1',
      })
    })
    expect(result.current.state.savedHash).toBe('hash-e1')

    // 放弃 E2、画布逐字节改回 E1：revert 后 savedHash 恰好是已持久化
    // 内容的身份，草稿卡核对不会拿陈旧身份短路。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: E1\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    expect(['saved', 'idle']).toContain(result.current.state.status)
    expect(result.current.state.savedHash).toBe('hash-e1')
  })
})
