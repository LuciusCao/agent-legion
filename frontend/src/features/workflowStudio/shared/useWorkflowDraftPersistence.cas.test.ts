/* #633 codex review P1-2/P2-1：CAS 写入与基线推进的持久化用例
   （#809：文件超 800 行纪律线，冲突呈现与冲突解决用例零改动迁出至
   useWorkflowDraftPersistence.cas.conflict/resolve.test.ts 姊妹文件；
   mock/setup 与各姊妹文件同构）。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DRAFT_NEVER_SAVED,
  WorkflowDraftConflictError,
} from '../../../api/workflowDraft'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'

const mocks = {
  fetchWorkflowDraft: vi.fn(),
  putWorkflowDraft: vi.fn(),
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

const SERVER_DRAFT = {
  definition_yaml: 'key: demo\nlabel: Server\n',
  updated_at: '2026-08-27T01:02:03+00:00',
}
const NO_DRAFT = { definition_yaml: null, updated_at: null }

type HookProps = {
  workspaceId: string | undefined
  draftYaml: string
  originalYaml: string
  serverDraft: typeof SERVER_DRAFT | typeof NO_DRAFT | undefined
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

describe('useWorkflowDraftPersistence CAS (#633)', () => {
  const BASE = 'key: demo\nlabel: Base\n'
  const EDITED = 'key: demo\nlabel: Edited\n'
  const SERVER_AT = '2026-08-27T01:02:03+00:00'

  function conflictError() {
    return new WorkflowDraftConflictError({
      message: 'Workflow draft conflict',
      current_draft: {
        definition_yaml: 'key: demo\nlabel: Agent v2\n',
        updated_at: '2026-09-12T10:00:00+00:00',
      },
    })
  }

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  it('PUTs with the hydrated updated_at as the CAS base', async () => {
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })

    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      expectedUpdatedAt: SERVER_AT,
    })
  })

  it('PUTs with never-saved before any baseline exists', async () => {
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })

    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
  })

  it('a superseded success still advances the CAS base for the follow-up save', async () => {
    /* codex review R2 P1：保存 A 在途时用户继续编辑调度 B——A 的成功
       响应虽被作废（不落 savedAt/不发通知），但它是服务端真值，基线必须
       前进；否则 B 携带 A 之前的旧基线必然 409，连续编辑被误报成
       「其它会话更新」并停止自动保存。 */
    const A_AT = '2026-09-12T11:00:00+00:00'
    let resolveA: (value: typeof SERVER_DRAFT) => void = () => {}
    mocks.putWorkflowDraft.mockImplementationOnce(
      () => new Promise((resolve) => (resolveA = resolve))
    )
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    // A：edited 草稿的保存（PUT 挂起在途）。
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)

    // A 在途时用户继续编辑 → B 的 debounce 调度作废 A 的 UI 结果。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Third\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    // A 成功返回（被 B 的调度作废）。
    await act(async () => {
      resolveA({ definition_yaml: EDITED, updated_at: A_AT })
    })
    // B 的 debounce 到期发起 PUT——基线必须是 A 落盘的时间戳。
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: Third\n',
      { expectedUpdatedAt: A_AT }
    )
  })

  // --- #633 codex review P2-1：conflict 响应推进 CAS 基线。 ---

  it('a conflict advances lastPersistedAt so the next save competes on the fresh base', async () => {
    // 冲突响应的 current_draft.updated_at 是服务端真值：进入 conflict 态的
    // 同时把基线推进到它——用户显式保留本页（resolveConflict）后的下一次
    // 保存以新基线发起，而不是永远用过期时间戳 409。
    mocks.putWorkflowDraft.mockRejectedValueOnce(conflictError())
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.conflict).toBe(true))
    // conflict 态的 savedAt 已是冲突响应携带的服务端时间戳。
    expect(result.current.state.savedAt).toBe('2026-09-12T10:00:00+00:00')

    // 用户继续编辑后显式保留本页：保存以冲突响应推进后的基线发起。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED,
      updated_at: '2026-09-12T11:30:00+00:00',
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: After conflict\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(result.current.state.conflict).toBe(true) // 挂起中，未发 PUT
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: After conflict\n',
      { expectedUpdatedAt: '2026-09-12T10:00:00+00:00' }
    )
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(result.current.state.conflict).toBeUndefined()
  })

  // --- #633 codex review P1-2：turn-end 失效后服务端草稿前进的重应用。 ---

  it('re-hydrates the CAS base when the server draft advanced and was adopted', async () => {
    // 画布采用了新服务端草稿（无本地编辑）：hydrate 把 lastPersistedAt
    // 推进到新 updated_at，随后的保存以新基线竞争（不过期时间戳）。
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    const ADVANCED = '2026-08-27T03:00:00+00:00'
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Agent v2\n',
      originalYaml: BASE,
      serverDraft: {
        definition_yaml: 'key: demo\nlabel: Agent v2\n',
        updated_at: ADVANCED,
      },
    })
    // 采用的草稿与 lastPersisted 一致：不触发回写 PUT。
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()

    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: My edit\n',
      originalYaml: BASE,
      serverDraft: {
        definition_yaml: 'key: demo\nlabel: Agent v2\n',
        updated_at: ADVANCED,
      },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: My edit\n',
      { expectedUpdatedAt: ADVANCED }
    )
  })

  // --- kimi review P1-1：own-save 回显不误报幻影冲突。 ---

  it("does not raise a phantom conflict when the refetched draft is the user's own save", async () => {
    // 用户编辑 → 保存成功 → turn-end 失效重取回自己的草稿：服务端 yaml
    // 与画布一致，即使 touched=true 也只推进基线（hydrate），不进冲突态。
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    // turn-end 重取：updated_at 推进到本页保存的时间戳，内容 === 画布。
    const OWN_AT = '2026-08-27T05:00:00+00:00'
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: EDITED, updated_at: OWN_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(result.current.state.conflict).toBeFalsy()
    expect(result.current.state.status).not.toBe('error')
  })
})
