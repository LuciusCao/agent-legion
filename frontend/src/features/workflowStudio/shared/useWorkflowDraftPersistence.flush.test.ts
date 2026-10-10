/* flushNow 立即落盘与 pagehide/beforeunload 卸载护栏主题的持久化用例
   （自 useWorkflowDraftPersistence.test.ts 按测试文件体积纪律拆出——
   #1177 codex R5 P1：存量超 800 行文件随触碰拆分，用例零改动迁移；
   mock/setup 与原文件同构）。 */
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

describe('useWorkflowDraftPersistence flushNow', () => {
  const BASE = 'key: demo\nlabel: Base\n'
  const EDITED = 'key: demo\nlabel: Edited\n'

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  it('saves pending edits immediately without waiting for the debounce', async () => {
    const { result, rerender } = renderPersistence({
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

    let flushed: { ok: boolean } | undefined
    await act(async () => {
      flushed = await result.current.flushNow()
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    // #429 收尾 P2-1：resolve 值携带本次落盘的终态（成功 → ok=true）。
    expect(flushed?.ok).toBe(true)
  })

  it('is a no-op when there is nothing unsaved', async () => {
    const { result } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })

    let flushed: { ok: boolean } | undefined
    await act(async () => {
      flushed = await result.current.flushNow()
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
    // no-op（无 pending）的 resolve 值：无内容需要落盘 = 无失败。
    expect(flushed?.ok).toBe(true)
  })

  it('flushNow resolves {ok: false} when the PUT fails through all retries (live terminal result)', async () => {
    // #429 收尾 P2-1 契约钉：DraftSaveController 全路径 resolve 不 reject，
    // 失败的终态只能经返回值传递（controller 的 live state 同步携带）——
    // 调用方（发布确认守卫）读 result.ok，不读 React useState 快照（闭包
    // 捕获的是调用前的值，await 期间落定的 error 态快照链路看不见）。
    mocks.putWorkflowDraft.mockRejectedValue(new Error('network down'))
    const { result, rerender } = renderPersistence({
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

    let flushed: { ok: boolean; state: { status: string } } | undefined
    await act(async () => {
      // 初次（debounce 立即发）+ 两次重试（2s/4s）全部失败。
      vi.advanceTimersByTime(850)
      vi.advanceTimersByTime(2000)
      vi.advanceTimersByTime(4000)
      flushed = await result.current.flushNow()
    })

    expect(flushed?.ok).toBe(false)
    expect(flushed?.state.status).toBe('error')
  })

  it('re-saves the current draft when clicked after retries ran out', async () => {
    mocks.putWorkflowDraft.mockRejectedValue(new Error('network'))
    const { result, rerender } = renderPersistence({
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
    // 初次 + 两次重试全部失败（1 + 2s + 4s）。
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    await act(async () => {
      vi.advanceTimersByTime(4000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(3)
    expect(result.current.state.status).toBe('error')

    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
    await act(async () => {
      result.current.flushNow()
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(4)
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith('ws1', EDITED, {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
  })
})

describe('useWorkflowDraftPersistence unload guard', () => {
  const BASE = 'key: demo\nlabel: Base\n'
  const EDITED = 'key: demo\nlabel: Edited\n'

  function renderEdited() {
    const rendered = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })
    rendered.rerender({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })
    return rendered
  }

  function dispatchBeforeUnload() {
    const event = new Event('beforeunload', { cancelable: true })
    act(() => {
      window.dispatchEvent(event)
    })
    return event
  }

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  it('flushes pending edits when the page becomes hidden', async () => {
    renderEdited()
    const visibility = vi
      .spyOn(document, 'visibilityState', 'get')
      .mockReturnValue('hidden')

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
    visibility.mockRestore()
  })

  it('flushes pending edits with keepalive on pagehide', async () => {
    renderEdited()

    await act(async () => {
      window.dispatchEvent(new Event('pagehide'))
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      keepalive: true,
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
  })

  it('does not flush on pagehide when the draft is clean', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })

    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('falls back to a plain PUT on pagehide when the UTF-8 body exceeds the keepalive limit', async () => {
    // 中文按 UTF-8 三字节计：2.5 万字符的草稿 body 超 60KiB 安全阈值，但
    // UTF-16 码元数远低于它——按码元数判断会误用 keepalive 导致发送失败。
    const hugeDraft = `key: demo\nlabel: ${'题'.repeat(25_000)}\n`
    const rendered = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })
    rendered.rerender({
      workspaceId: 'ws1',
      draftYaml: hugeDraft,
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })

    await act(async () => {
      window.dispatchEvent(new Event('pagehide'))
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', hugeDraft, {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    })
  })

  it('blocks page unload while edits are unsaved and stays quiet once saved', async () => {
    renderEdited()

    expect(dispatchBeforeUnload().defaultPrevented).toBe(true)

    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(dispatchBeforeUnload().defaultPrevented).toBe(false)
  })

  it('blocks page unload for in-memory edits while the draft query has not resolved', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: EDITED,
      originalYaml: BASE,
      serverDraft: undefined,
    })

    expect(dispatchBeforeUnload().defaultPrevented).toBe(true)
  })

  it('does not block page unload before hydration when the draft matches the baseline', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: undefined,
    })

    expect(dispatchBeforeUnload().defaultPrevented).toBe(false)
  })

  it('merges the draft query error into the exposed state', () => {
    const { result } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: BASE,
      originalYaml: BASE,
      serverDraft: undefined,
      loadError: true,
    })

    expect(result.current.state.loadError).toBe(true)
  })
})
