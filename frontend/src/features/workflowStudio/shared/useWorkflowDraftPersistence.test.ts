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
}

function renderPersistence(initial: HookProps) {
  return renderHook(
    (props: HookProps) =>
      useWorkflowDraftPersistence(
        props.workspaceId,
        props.draftYaml,
        props.originalYaml,
        props.serverDraft
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

    expect(result.current).toEqual({
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
    await waitFor(() => expect(result.current.status).toBe('saved'))
    expect(result.current.savedAt).toBe(SERVER_DRAFT.updated_at)
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

    await waitFor(() => expect(result.current.status).toBe('error'))
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
  it('prioritizes saving and error over the saved-at time', () => {
    const savedAt = '2026-08-27T09:05:00+00:00'
    expect(draftSaveText({ status: 'saving', savedAt })).toBe('草稿保存中…')
    expect(draftSaveText({ status: 'error', savedAt })).toContain(
      '草稿自动保存失败'
    )
    expect(draftSaveText({ status: 'idle', savedAt: null })).toBeNull()
    expect(draftSaveText(undefined)).toBeNull()
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
