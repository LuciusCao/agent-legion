/* #633 codex review P1-2/P2-1：CAS 冲突与 turn-end 重应用的持久化用例
   （自 useWorkflowDraftPersistence.test.ts 按测试文件体积纪律拆出，
   用例零改动迁移；mock/setup 与原文件同构）。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DRAFT_NEVER_SAVED,
  WorkflowDraftConflictError,
} from '../../../api/workflowDraft'
import {
  draftSaveText,
  useWorkflowDraftPersistence,
} from './useWorkflowDraftPersistence'

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
      }
      this.currentDraft = {
        definition_yaml: current.definition_yaml ?? null,
        updated_at: current.updated_at ?? null,
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

/* consume 是稳定的闭包（内部读外部可变的 pending），rerender 只需换 props。 */
function renderHookResult(
  initial: HookProps,
  consume: () => { yaml: string; updatedAt: string } | null
) {
  return renderHook(
    (props: HookProps) =>
      useWorkflowDraftPersistence(
        props.workspaceId,
        props.draftYaml,
        props.originalYaml,
        props.serverDraft,
        props.loadError,
        consume
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

  it('a 409 conflict lands in the conflict state without retrying', async () => {
    mocks.putWorkflowDraft.mockRejectedValue(conflictError())
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

    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.state.conflict).toBe(true))
    expect(result.current.state.conflictDraftYaml).toBe(
      'key: demo\nlabel: Agent v2\n'
    )
    // 冲突不自动重试：同一过期时间戳重试只会再 409。
    await act(async () => {
      vi.advanceTimersByTime(10000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
    // kimi review P2-8：冲突文案带行动指引（挂起自动保存 + 二选一）。
    expect(draftSaveText(result.current.state)).toBe(
      'Agent 已保存新的草稿版本；本页编辑未落盘，自动保存已暂停——请选择采用 Agent 版本或保留本页编辑'
    )
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

  it('a successful save after a conflict updates the CAS base and clears the flag on the next edit', async () => {
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

    // kimi review P1-2/P2-4：冲突后继续编辑不再自动保存（挂起 autosave，
    // 防止对 Agent 改动零知情下不可逆覆盖）；编辑进 pendingSave 待显式解除。
    const callsBefore = mocks.putWorkflowDraft.mock.calls.length
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Third\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft.mock.calls.length).toBe(callsBefore)
    expect(result.current.state.conflict).toBe(true)

    // 用户显式选择保留本页编辑：以冲突响应推进后的基线重新竞争。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: demo\nlabel: Third\n',
      updated_at: '2026-09-12T11:00:00+00:00',
    })
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(result.current.state.conflict).toBeUndefined()

    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Fourth\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: Fourth\n',
      { expectedUpdatedAt: '2026-09-12T11:00:00+00:00' }
    )
  })

  it('flushNow resolves {ok: false} on a conflict (publish guard must abort)', async () => {
    mocks.putWorkflowDraft.mockRejectedValue(conflictError())
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

    let flushed: { ok: boolean; state: { conflict?: boolean } } | undefined
    await act(async () => {
      flushed = await result.current.flushNow()
    })

    expect(flushed?.ok).toBe(false)
    expect(flushed?.state.conflict).toBe(true)
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

  it('a conflict keeps the user edits on the canvas (never silently reverted)', async () => {
    // 冲突只呈现（conflict 态 + conflictDraftYaml 供采用入口），画布上的
    // 用户编辑原样保留——由用户决定采用服务端草稿还是继续编辑。
    mocks.putWorkflowDraft.mockRejectedValue(conflictError())
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

    // 编辑值（draftYaml prop）不因冲突回退；hasUnsavedChanges 保持 true
    // （冲突内容未落盘，离开页面前 unload 守卫必须拦截）。
    expect(result.current.hasUnsavedChanges()).toBe(true)
    expect(result.current.state.conflictDraftYaml).toBe(
      'key: demo\nlabel: Agent v2\n'
    )
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

  it('surfaces a server-advance conflict and preserves edits when the reapply conflict fires', async () => {
    // 服务端草稿前进且用户有本地编辑：surfaceServerConflict 进入 conflict
    // 态（conflictDraftYaml = 服务端草稿），编辑保留，基线已推进——用户
    // 下一次保存以新基线竞争。
    const conflict = {
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T03:00:00+00:00',
    }
    let pending: typeof conflict | null = null
    const consume = () => {
      const value = pending
      pending = null
      return value
    }
    const { result, rerender } = renderHookResult(
      {
        workspaceId: 'ws1',
        draftYaml: EDITED,
        originalYaml: BASE,
        serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
      },
      consume
    )
    // turn-end 失效：服务端草稿前进，画布保留用户编辑 → 冲突通知。
    pending = conflict
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: {
        definition_yaml: conflict.yaml,
        updated_at: conflict.updatedAt,
      },
    })
    await waitFor(() => expect(result.current.state.conflict).toBe(true))
    expect(result.current.state.conflictDraftYaml).toBe(conflict.yaml)

    // 基线已推进到服务端真值。kimi review P2-4：冲突态挂起自动保存——
    // 继续编辑不自动 PUT；显式 resolveConflict(true) 后以推进的基线竞争。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED,
      updated_at: '2026-08-27T04:00:00+00:00',
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: After conflict\n',
      originalYaml: BASE,
      serverDraft: {
        definition_yaml: conflict.yaml,
        updated_at: conflict.updatedAt,
      },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(result.current.state.conflict).toBe(true) // 挂起：未自动 PUT
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: After conflict\n',
      { expectedUpdatedAt: conflict.updatedAt }
    )
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
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

  // --- kimi review P1-2：adoptServerDraft 出口。 ---

  it('adoptServerDraft takes the agent version, advances the base, and clears the conflict', async () => {
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

    const adopted: string[] = []
    act(() =>
      result.current.adoptServerDraft(
        'key: demo\nlabel: Agent v2\n',
        '2026-09-12T10:00:00+00:00',
        (yaml) => adopted.push(yaml)
      )
    )
    expect(result.current.state.conflict).toBeFalsy()
    expect(adopted).toEqual(['key: demo\nlabel: Agent v2\n'])
    // 采用后画布（调用方写入）= 服务端内容：后续调度不发起覆盖性 PUT。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Agent v2\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    const calls = mocks.putWorkflowDraft.mock.calls.length
    expect(calls).toBe(1) // 仅第一次保存；adopt 未触发回写
  })
})
