/* #633 codex review P1-2/P2-1：CAS 冲突解决（resolveConflict /
   adoptServerDraft）与 turn-end 重应用冲突的持久化用例
   （自 useWorkflowDraftPersistence.cas.test.ts 按测试文件体积纪律拆出，
   用例零改动迁移；mock/setup 与原文件同构）。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftConflictError } from '../../../api/workflowDraft'
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

type HookProps = {
  workspaceId: string | undefined
  draftYaml: string
  originalYaml: string
  serverDraft:
    | typeof SERVER_DRAFT
    | { definition_yaml: null; updated_at: null }
    | undefined
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
  consume: () => { yaml: string; updatedAt: string; hash: string | null } | null
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

describe('useWorkflowDraftPersistence CAS (#633)：冲突解决与重应用', () => {
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

  it('keep-mine with an empty pendingSave (conflict arrived in-flight) re-saves the current canvas content（#804 P1-A：否则卡死 error 永不落盘）', async () => {
    // 409 在 PUT 在途时到达：enterConflict 已把 pendingSave 清空——
    // resolveConflict(true) 拿不到 pending，旧实现到此为止：状态停 error、
    // 调度 effect 因 draftYaml 未变不再触发、flushNow no-op，编辑静默丢失。
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

    // 不再做任何编辑，直接「保留本页编辑」：必须按当前画布内容补发保存。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED,
      updated_at: '2026-09-12T11:00:00+00:00',
    })
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith('ws1', EDITED, {
      expectedUpdatedAt: '2026-09-12T10:00:00+00:00',
    })
    expect(result.current.state.conflict).toBeUndefined()
  })

  it('resolveConflict(false)（仅解除警示）收敛 status 到 saved/idle，不留假 error（#804 轮 6 H6）', async () => {
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
    expect(result.current.state.status).toBe('error')

    act(() => result.current.resolveConflict(false))
    // 冲突标记清除 + status 收敛（savedAt 已推进到服务端真值 → saved）。
    expect(result.current.state.conflict).toBeFalsy()
    expect(result.current.state.status).toBe('saved')
  })

  it('resolveConflict(false) 不遗忘冲突期间的未落盘编辑（#1177 评审 V3：保留挂起 pending）', async () => {
    // UI 只在无可采用草稿（conflictDraftYaml == null 的 never-saved 竞态）
    // 时走「仅解除警示」——丢弃挂起的 pendingSave 会让画布上的编辑被状态机
    // 遗忘（status 假 saved、hasUnsaved=false，关页静默丢失）。
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

    // 冲突期间继续编辑：挂起 autosave（不进 PUT），编辑进 pendingSave。
    const callsBefore = mocks.putWorkflowDraft.mock.calls.length
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: During conflict\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft.mock.calls.length).toBe(callsBefore)

    act(() => result.current.resolveConflict(false))
    expect(result.current.state.conflict).toBeFalsy()
    // 未落盘编辑保留为 pending（不 arm 计时器）：离开假 settled，
    // beforeunload 守卫有原料。
    expect(result.current.state.status).toBe('pending')
    expect(result.current.hasUnsavedChanges()).toBe(true)

    // 下一次按键重新 arm 并以冲突推进后的基线落盘。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: demo\nlabel: Final\n',
      updated_at: '2026-09-12T11:00:00+00:00',
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Final\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: Final\n',
      { expectedUpdatedAt: '2026-09-12T10:00:00+00:00' }
    )
  })

  it('resolveConflict(true) 补发挂起保存前先把 status 推到 pending（#1177 评审 P3-1）', async () => {
    // 修复前 conflictCleared 收敛的 saved 会覆盖整个补发 debounce 窗口——
    // 窗口内「settled 但内容未落盘」的假状态与 schedule 路径不对称。
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
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: During conflict\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })

    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: demo\nlabel: During conflict\n',
      updated_at: '2026-09-12T11:00:00+00:00',
    })
    act(() => result.current.resolveConflict(true))
    // 补发 debounce 窗口内：status 已是 pending（非假 saved）。
    expect(result.current.state.status).toBe('pending')
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: During conflict\n',
      { expectedUpdatedAt: '2026-09-12T10:00:00+00:00' }
    )
  })

  it('surfaces a server-advance conflict and preserves edits when the reapply conflict fires', async () => {
    // 服务端草稿前进且用户有本地编辑：surfaceServerConflict 进入 conflict
    // 态（conflictDraftYaml = 服务端草稿），编辑保留，基线已推进——用户
    // 下一次保存以新基线竞争。
    const conflict = {
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T03:00:00+00:00',
      hash: null,
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

  it('keep-mine after conflict with canvas == lastPersisted still forces the write-back（#804 轮 8 P1：去重吞补救的洞）', async () => {
    // 场景：本页内容 A 已保存（lastPersisted=A）→ Agent 推进服务端为 B →
    // reapply 冲突（pendingSave 早空）→ 用户「保留本页编辑」。补救路径若走
    // 普通 schedule(A)，去重把 A 判为已持久化 → revert 不发 PUT——警示消失
    // 但 A 从未写回，离页即丢。断言到 PUT 真发出（请求体 A + 新 CAS 基线）
    // 并落定 saved。
    const conflict = {
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T03:00:00+00:00',
      hash: null,
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
        draftYaml: BASE,
        originalYaml: BASE,
        serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
      },
      consume
    )
    // 先编辑 A（EDITED）并落盘：lastPersisted=A。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED,
      updated_at: '2026-08-27T02:00:00+00:00',
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
    const callsBeforeConflict = mocks.putWorkflowDraft.mock.calls.length

    // Agent 推进服务端草稿为 B → reapply 冲突（画布仍 = A）。
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

    // keep-mine：必须按新 CAS 基线把 A 强制写回（绕过去重）。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: EDITED,
      updated_at: '2026-08-27T04:00:00+00:00',
    })
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft.mock.calls.length).toBeGreaterThan(
      callsBeforeConflict
    )
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith('ws1', EDITED, {
      expectedUpdatedAt: conflict.updatedAt,
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
  })

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
