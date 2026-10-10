/* #633 codex review P1-2/P2-1：CAS 冲突呈现与挂起语义的持久化用例
   （自 useWorkflowDraftPersistence.cas.test.ts 按测试文件体积纪律拆出，
   用例零改动迁移；mock/setup 与原文件同构）。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftConflictError } from '../../../api/workflowDraft'
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

describe('useWorkflowDraftPersistence CAS (#633)：冲突呈现与挂起', () => {
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

  it('flushNow in conflict state resolves {ok: false}（#804 轮 6 H1：冲突态 no-op 不得报 ok，否则 agent 发布确认守卫放行审 A 发 B）', async () => {
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

    // 冲突态的 flushNow 是有意 no-op（不静默覆盖 Agent 草稿）——但终态
    // 必须 ok:false（与 draftSaveQueue 的 drain 路径同语义），等待方
    // （agent 发布确认）据此中止。
    let flushed: { ok: boolean } | undefined
    await act(async () => {
      flushed = await result.current.flushNow()
    })
    expect(flushed?.ok).toBe(false)
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

  it('entering the conflict state cancels the pending debounce timer (codex R4 P1)', async () => {
    // 用户编辑已 arm 的 debounce 计时器若在 enterConflict 后存活，到期 save()
    // 会用刚推进的服务端时间戳成功覆盖 Agent 版本——绕过显式二选一。
    const { result, rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    // 一次失败的保存先进入 conflict 态。
    mocks.putWorkflowDraft.mockRejectedValueOnce(conflictError())
    rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(850)
    })
    await waitFor(() => expect(result.current.state.conflict).toBe(true))
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
    // conflict 态下再编辑（挂起 pendingSave，不 arm 计时器——kimi P1-2）；
    // 即使计时器意外存活，conflict 置位后 flushNow 也不发 PUT；推进大量
    // 假时钟证明没有任何计时器在途。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: More\n',
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1) // 无覆盖性 PUT
  })

  it('冲突期间把画布改回冲突前内容不再自动解除冲突（#1195：须显式二选一）', async () => {
    // enterConflict 已把 CAS 基线推进到服务端 updated_at（Agent 版本 D2）。
    // 此时画布逐字节回到冲突前的本地内容 D1，「画布 == D1」≠「画布 == 服务端
    // D2」——自动解除冲突会让 savedAt（服务端 t2）与画布内容（D1）身份自相
    // 矛盾，且下一次编辑以 t2 基线静默覆盖 D2。
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

    // 画布改回冲突前内容：冲突横幅保留（revert 不再自动解除），不发 PUT。
    rerender({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: { definition_yaml: BASE, updated_at: SERVER_AT },
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(result.current.state.conflict).toBe(true)
    expect(result.current.state.status).toBe('error')
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)

    // 显式 keep-mine 才解除冲突：以推进后的基线把画布内容写回（用户已看过
    // 警示，覆盖 Agent 版本是显式选择而非静默发生）。
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: BASE,
      updated_at: '2026-09-12T11:00:00+00:00',
    })
    act(() => result.current.resolveConflict(true))
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith('ws1', BASE, {
      expectedUpdatedAt: '2026-09-12T10:00:00+00:00',
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(result.current.state.conflict).toBeFalsy()
  })
})
