import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  draftSaveText,
  useWorkflowDraftPersistence,
} from './useWorkflowDraftPersistence'

const mocks = {
  fetchWorkflowDraft: vi.fn(),
  putWorkflowDraft: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchWorkflowDraft: (...args: unknown[]) => mocks.fetchWorkflowDraft(...args),
  putWorkflowDraft: (...args: unknown[]) => mocks.putWorkflowDraft(...args),
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

describe('useWorkflowDraftPersistence', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  it('does not PUT before the server draft query resolves', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: undefined,
    })

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('does not PUT the baseline over a missing server draft after hydration', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('does not re-PUT the server draft value that was just applied', () => {
    renderPersistence({
      workspaceId: 'ws1',
      draftYaml: SERVER_DRAFT.definition_yaml,
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: SERVER_DRAFT,
    })

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('exposes the server draft saved-at after hydration', () => {
    const { result } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: SERVER_DRAFT.definition_yaml,
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: SERVER_DRAFT,
    })

    expect(result.current.state).toEqual({
      status: 'idle',
      savedAt: '2026-08-27T01:02:03+00:00',
    })
  })

  it('PUTs edits after an 800ms debounce and reports saved', async () => {
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
    act(() => {
      vi.advanceTimersByTime(700)
    })
    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()

    await act(async () => {
      vi.advanceTimersByTime(150)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Edited\n'
    )
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
    expect(result.current.state.savedAt).toBe(SERVER_DRAFT.updated_at)
  })

  it('overwrites the server draft after publish rebases the baseline', async () => {
    // 用户在 debounce 窗口内 publish：草稿 Y 尚未持久化，基线前进为 Y，
    // 效果仍必须把 Y PUT 上去（否则旧草稿 X 会在下次装载时复活）。
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: {
        definition_yaml: 'key: demo\nlabel: Base\n',
        updated_at: '2026-08-27T00:00:00+00:00',
      },
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Y\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: {
        definition_yaml: 'key: demo\nlabel: Base\n',
        updated_at: '2026-08-27T00:00:00+00:00',
      },
    })
    // publish + reload：基线与草稿同为 Y（草稿未变，不再 rerender draftYaml）。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Y\n',
      originalYaml: 'key: demo\nlabel: Y\n',
      serverDraft: {
        definition_yaml: 'key: demo\nlabel: Base\n',
        updated_at: '2026-08-27T00:00:00+00:00',
      },
    })

    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Y\n'
    )
  })

  it('reports error when the PUT fails', async () => {
    mocks.putWorkflowDraft.mockRejectedValue(new Error('network'))
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
      vi.advanceTimersByTime(850)
    })

    await waitFor(() => expect(result.current.state.status).toBe('error'))
  })

  it('saves edits made while the draft query was in flight once hydration lands', async () => {
    // GET 在途时用户已编辑：保存 effect 因未 hydrated 提前退出，draftYaml
    // 之后不再变化；hydration 翻转必须重新评估当前草稿并补这次保存。
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: undefined,
    })
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()

    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Edited\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Edited\n'
    )
  })

  it('does not PUT the baseline over an existing server draft on hydration', async () => {
    // hydration 触发的重新评估以服务端草稿为已持久化基线：draftYaml 等于
    // 服务端草稿（组合层刚应用完）时不得发起任何 PUT。
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: undefined,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: SERVER_DRAFT.definition_yaml,
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: SERVER_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })

    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('invalidates an in-flight PUT and re-saves when the draft reverts to the persisted value', async () => {
    // B 的 PUT 在途时用户回退到已持久化值 A：在途响应必须作废（不得把
    // lastPersisted 更新为 B），并补存 A 把服务端可能已收到的 B 改回来。
    let resolvePut: (value: typeof SERVER_DRAFT) => void = () => {}
    mocks.putWorkflowDraft.mockImplementation(
      () =>
        new Promise<typeof SERVER_DRAFT>((resolve) => {
          resolvePut = resolve
        })
    )
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: A\n',
      originalYaml: 'key: demo\nlabel: A\n',
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: B\n',
      originalYaml: 'key: demo\nlabel: A\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: B\n'
    )

    // PUT(B) 响应前回退到 A。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: A\n',
      originalYaml: 'key: demo\nlabel: A\n',
      serverDraft: NO_DRAFT,
    })
    // B 的响应迟到：应被作废，lastPersisted 仍是 A。
    await act(async () => {
      resolvePut({
        definition_yaml: 'key: demo\nlabel: B\n',
        updated_at: '2026-08-27T02:00:00+00:00',
      })
    })
    // 回退后补存 A（last-write-wins 把服务端的 B 改回来）。
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: A\n'
    )

    // lastPersisted 未被 B 污染：再编辑为 C 照常保存。
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: C\n',
      originalYaml: 'key: demo\nlabel: A\n',
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: C\n'
    )
  })

  it('debounces rapid edits into a single PUT of the latest value', async () => {
    const { rerender } = renderPersistence({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Base\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: One\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })
    act(() => {
      vi.advanceTimersByTime(400)
    })
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Two\n',
      originalYaml: 'key: demo\nlabel: Base\n',
      serverDraft: NO_DRAFT,
    })

    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Two\n'
    )
  })
})

describe('draftSaveText', () => {
  it('prioritizes saving, error and pending over the saved-at time', () => {
    const savedAt = '2026-08-27T09:05:00+00:00'
    expect(draftSaveText({ status: 'saving', savedAt })).toBe('草稿保存中…')
    expect(draftSaveText({ status: 'error', savedAt })).toBe(
      '草稿保存失败，将自动重试'
    )
    expect(draftSaveText({ status: 'pending', savedAt })).toBe(
      '草稿有未保存更改'
    )
    expect(draftSaveText({ status: 'idle', savedAt: null })).toBeNull()
    expect(draftSaveText(undefined)).toBeNull()
  })

  it('shows the service-unavailable warning when the draft query failed', () => {
    expect(
      draftSaveText({ status: 'idle', savedAt: null, loadError: true })
    ).toBe('草稿服务不可用，编辑仅保留在本页内存')
  })

  it('formats the saved-at time as HH:MM', () => {
    const savedAt = '2026-08-27T09:05:00+00:00'
    const at = new Date(savedAt)
    const hh = String(at.getHours()).padStart(2, '0')
    const mm = String(at.getMinutes()).padStart(2, '0')
    expect(draftSaveText({ status: 'saved', savedAt })).toBe(
      `草稿已保存 ${hh}:${mm}`
    )
  })
})

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

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED)
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
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith('ws1', EDITED)
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
  })
})

describe('useWorkflowDraftPersistence PUT retry', () => {
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

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER_DRAFT)
  })

  it('retries a failed PUT with backoff and keeps error after retries run out', async () => {
    mocks.putWorkflowDraft.mockRejectedValue(new Error('network'))
    const { result } = renderEdited()

    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.status).toBe('error'))
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(1)

    // 第一次重试（+2s）仍失败。
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(2)

    // 第二次重试（+4s）仍失败：重试耗尽，停留 error，不再发起第四次。
    await act(async () => {
      vi.advanceTimersByTime(4000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(3)
    await act(async () => {
      vi.advanceTimersByTime(10000)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(3)
    expect(result.current.state.status).toBe('error')
  })

  it('recovers to saved when a retry succeeds', async () => {
    mocks.putWorkflowDraft.mockRejectedValueOnce(new Error('network'))
    const { result } = renderEdited()

    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() => expect(result.current.state.status).toBe('error'))

    await act(async () => {
      vi.advanceTimersByTime(2000)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(result.current.state.status).toBe('saved'))
  })

  it('lets a newer edit supersede a pending retry', async () => {
    mocks.putWorkflowDraft.mockRejectedValueOnce(new Error('network'))
    const { rerender } = renderEdited()

    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED)

    // 重试计时器等待中来了新编辑：旧重试必须作废，只保存最新值。
    rerender({
      workspaceId: 'ws1',
      draftYaml: 'key: demo\nlabel: Two\n',
      originalYaml: BASE,
      serverDraft: NO_DRAFT,
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await act(async () => {
      vi.advanceTimersByTime(10000)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledTimes(2)
    expect(mocks.putWorkflowDraft).toHaveBeenLastCalledWith(
      'ws1',
      'key: demo\nlabel: Two\n'
    )
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

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED)
    visibility.mockRestore()
  })

  it('flushes pending edits with keepalive on pagehide', async () => {
    renderEdited()

    await act(async () => {
      window.dispatchEvent(new Event('pagehide'))
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', EDITED, {
      keepalive: true,
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

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith('ws1', hugeDraft)
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
