import { renderHook, waitFor } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { useUiStore } from '../../../stores/uiStore'
import type { DraftSaveStatus } from './draftSaveTypes'
import type { UseWorkflowStudioDraftResult } from './useWorkflowStudioDraft'
import type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare'

const mocks = {
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  publishWorkflowDraft: (...args: unknown[]) =>
    mocks.publishWorkflowDraft(...args),
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
}))

type DraftWithSave = UseWorkflowStudioDraftResult & {
  draftSave: { status: DraftSaveStatus; savedAt: string | null }
}

const draft: DraftWithSave = {
  draftYaml: 'key: demo\n',
  setDraftYaml: vi.fn(),
  definitionYaml: 'key: demo\n',
  visibleWorkflow: null,
  visibleRevision: null,
  readOnly: false,
  dirty: true,
  canSubmit: true,
  viewMode: 'draft',
  selectedRevisionId: null,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
  draftSave: { status: 'idle', savedAt: null },
  markDraftPublished: vi.fn(),
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

const compare: UseWorkflowDraftCompareResult = {
  compareState: 'idle',
  compareResponse: null,
  compareErrors: null,
  compareSummary: null,
}

const compareWithChanges: UseWorkflowDraftCompareResult = {
  compareState: 'ready',
  compareResponse: null,
  compareErrors: null,
  compareSummary: {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [
      {
        type: 'modified',
        nodeKey: 'a',
        label: 'A',
        nodeType: 'code',
        fields: [],
        severity: 'info',
      },
    ],
    edgeChanges: [],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(['a']),
  },
}

const reload = vi.fn().mockResolvedValue(undefined)

type AutoProps = {
  saveStatus: DraftSaveStatus
  definitionYaml: string
  canSubmit?: boolean
}

function renderActionsHook(initial: AutoProps) {
  return renderHook(
    ({ saveStatus, definitionYaml, canSubmit }: AutoProps) =>
      useWorkflowStudioActions(
        'ws1',
        {
          ...draft,
          definitionYaml,
          canSubmit: canSubmit ?? true,
          draftSave: { status: saveStatus, savedAt: null },
        },
        reload,
        compare
      ),
    { initialProps: initial }
  )
}

/** 驱动一次「保存成功」边沿：idle → saved。 */
async function flushSaved(
  rerender: (props: AutoProps) => void,
  props: AutoProps
) {
  await act(async () => {
    rerender({ ...props, saveStatus: 'saved' })
  })
}

describe('useWorkflowStudioActions（#804 定案：自动校验）', () => {
  beforeEach(() => {
    useUiStore.setState({ toast: null })
    vi.clearAllMocks()
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
  })

  it('草稿保存成功后自动静默校验：结果写 validation state，不弹 toast', async () => {
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    expect(mocks.validateWorkflowDraft).not.toHaveBeenCalled()

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\n'
    )
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.publishing).toBe(false)
    // 静默：不弹 toast（手动校验时代有 toast）。
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('校验失败：写 errors + 校验失败 message（chip 变红、发布禁用的数据源）', async () => {
    mocks.validateWorkflowDraft.mockResolvedValue({
      valid: false,
      errors: ['missing key'],
    })
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    expect(result.current.validationMessage).toBe('校验失败')
    expect(result.current.validationErrors).toEqual(['missing key'])
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('传输失败自动退避重试，恢复后校验通过（codex 轮 4 P1-1）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.validateWorkflowDraft
        .mockRejectedValueOnce(new Error('network error'))
        .mockRejectedValueOnce(new Error('network error'))
        .mockResolvedValue({ valid: true, errors: [] })
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })

      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      // 第一次传输失败：不写终态 message（不写「校验失败：…」让 effect
      // 永远闭锁——旧实现此处已是终态，revert 即红），等退避重试。
      expect(result.current.validationMessage).toBe('')
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

      await act(async () => {
        vi.advanceTimersByTime(2100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(2)
      expect(result.current.validationMessage).toBe('')

      await act(async () => {
        vi.advanceTimersByTime(4100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(3)
      expect(result.current.validationMessage).toBe('校验通过')
    } finally {
      vi.useRealTimers()
    }
  })

  it('传输失败重试耗尽才写终态 校验失败：…（结构失败不重试）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.validateWorkflowDraft.mockRejectedValue(new Error('network error'))
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })

      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      expect(result.current.validationMessage).toBe('')

      // 3 次退避重试（2s/4s/8s）全部失败 → 终态。
      await act(async () => {
        vi.advanceTimersByTime(2100)
      })
      await act(async () => {
        vi.advanceTimersByTime(4100)
      })
      await act(async () => {
        vi.advanceTimersByTime(8100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(4)
      expect(result.current.validationMessage).toBe('校验失败：network error')
      expect(result.current.publishing).toBe(false)
      // 终态后不再自动重试（编辑→落盘会重新触发，见它例）。
      await act(async () => {
        vi.advanceTimersByTime(30000)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('干净态保存成功不触发校验（canSubmit=false：无未发布变更）', async () => {
    const { rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
      canSubmit: false,
    })

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
      canSubmit: false,
    })

    expect(mocks.validateWorkflowDraft).not.toHaveBeenCalled()
  })

  it('saved 常驻期间不重复触发（只在进入 saved 的边沿跑）', async () => {
    const { rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

    // saved → saved（无变化的重复渲染/刷新）：不触发。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
  })

  it('草稿再编辑后旧校验结果作废（回「未发布变更」的数据源）', async () => {
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    expect(result.current.validationMessage).toBe('校验通过')

    // 再编辑 → 进入 debounce 窗口（pending）：旧结果作废，且不触发新校验
    // （窗口内按未校验处理，codex 轮 3 P2）。
    await act(async () => {
      rerender({
        saveStatus: 'pending',
        definitionYaml: 'key: demo\nlabel: changed\n',
      })
    })
    expect(result.current.validationMessage).toBe('')
    expect(result.current.validationErrors).toEqual([])
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
  })

  it('hydrate 恢复的服务端草稿（idle + savedAt 非空，无 saved 边沿）也自动校验（codex 轮 3 P2）', async () => {
    renderHook(() =>
      useWorkflowStudioActions(
        'ws1',
        {
          ...draft,
          draftSave: { status: 'idle', savedAt: '2026-08-27T09:05:00+00:00' },
        },
        reload,
        compare
      )
    )

    await waitFor(() =>
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
        'ws1',
        'key: demo\n'
      )
    )
  })

  it('发布门控绑定当前 YAML：校验通过前 canPublish=false，通过后 true（codex 轮 3 P2）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null },
          },
          reload,
          compareWithChanges
        ),
      { initialProps: { saveStatus: 'idle', definitionYaml: 'key: demo\n' } }
    )
    // 有变更但尚未校验：不放行（旧门控此处即 true——revert 门控即红）。
    expect(result.current.canPublish).toBe(false)

    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)
  })

  it('审 A 发 B：确认框打开期间 YAML 被后台换掉 → reviewStale（禁确认数据源），关闭复位（轮 4 P2-C）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null },
          },
          reload,
          compareWithChanges
        ),
      { initialProps: { saveStatus: 'idle', definitionYaml: 'key: demo\n' } }
    )
    // 落盘 + 自动校验通过 → 可发布。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    expect(result.current.canPublish).toBe(true)

    act(() => result.current.requestPublish())
    expect(result.current.reviewDialogOpen).toBe(true)
    expect(result.current.reviewStale).toBe(false)

    // 打开期间 agent turn-end 把画布 YAML 换掉 → 审阅对象已漂移。
    await act(async () => {
      rerender({
        saveStatus: 'saved',
        definitionYaml: 'key: demo\nlabel: swapped\n',
      })
    })
    expect(result.current.reviewStale).toBe(true)

    act(() => result.current.closeReviewDialog())
    expect(result.current.reviewDialogOpen).toBe(false)
    expect(result.current.reviewStale).toBe(false)
  })

  it('校验在途期间草稿再编辑：迟到的结果丢弃，不覆盖新编辑', async () => {
    let resolveValidation!: (value: {
      valid: boolean
      errors: string[]
    }) => void
    mocks.validateWorkflowDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveValidation = resolve
        })
    )
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    // 保存成功 → 校验在途。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
    expect(result.current.validating).toBe(true)

    // 校验未回，草稿已改（旧结果即失效）。
    await act(async () => {
      rerender({
        saveStatus: 'pending',
        definitionYaml: 'key: demo\nlabel: newer\n',
      })
    })
    expect(result.current.validationMessage).toBe('')

    // 迟到结果抵达：不得写入。
    await act(async () => {
      resolveValidation!({ valid: true, errors: [] })
    })
    expect(result.current.validationMessage).toBe('')
    expect(result.current.publishing).toBe(false)
  })

  it('发布与校验的在途状态互不覆盖（codex 轮 5 P1：交错序列）', async () => {
    let resolvePublish!: (v: { valid: boolean; errors: string[] }) => void
    mocks.publishWorkflowDraft.mockImplementation(
      () =>
        new Promise((r) => {
          resolvePublish = r
        })
    )
    let resolveValidate!: (v: { valid: boolean; errors: string[] }) => void
    mocks.validateWorkflowDraft.mockImplementation(
      () =>
        new Promise((r) => {
          resolveValidate = r
        })
    )
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    // 落盘 → 自动校验在途。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    expect(result.current.validating).toBe(true)
    // 校验在途时发起发布（发布在途）。
    await act(async () => {
      void result.current.publishDraft()
    })
    expect(result.current.publishing).toBe(true)
    // 校验先结束：发布在途标记不得被清（共享 actionState 的旧实现这里
    // 会被盖回 idle → 发布入口提前解禁）。
    await act(async () => {
      resolveValidate({ valid: true, errors: [] })
    })
    expect(result.current.validating).toBe(false)
    expect(result.current.publishing).toBe(true)
    // 发布后结束：校验态已复位，不受发布影响。
    await act(async () => {
      resolvePublish({ valid: true, errors: [] })
    })
    expect(result.current.publishing).toBe(false)
    expect(result.current.validating).toBe(false)
  })

  it('sets validation failure message and clears errors on publish rejection', async () => {
    mocks.publishWorkflowDraft.mockRejectedValue(new Error('network error'))
    const { result } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await act(async () => {
      await result.current.publishDraft()
    })

    expect(result.current.publishing).toBe(false)
    expect(result.current.validationMessage).toBe('保存失败：network error')
    expect(result.current.validationErrors).toEqual([])
  })

  it('toasts publish outcome（发布仍弹 toast，与静默校验相对）', async () => {
    const { result } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await act(async () => {
      await result.current.publishDraft()
    })
    expect(useUiStore.getState().toast).toEqual({
      message: '保存成功',
      type: 'success',
    })
  })

  it('marks the published draft before reload so baseline sync force-resets (#666)', async () => {
    const markDraftPublished = vi.fn()
    const reloadCalls: string[] = []
    const trackingReload = vi.fn(async () => {
      // 登记必须先于 reload：baseline sync 在 reload 拉回的基线变化里消费标记。
      reloadCalls.push(
        markDraftPublished.mock.calls.length
          ? 'marked-before-reload'
          : 'not-marked'
      )
    })
    const { result } = renderHook(() =>
      useWorkflowStudioActions(
        'ws1',
        { ...draft, markDraftPublished },
        trackingReload,
        compare
      )
    )

    await act(async () => {
      await result.current.publishDraft()
    })

    expect(markDraftPublished).toHaveBeenCalledWith('key: demo\n')
    expect(reloadCalls).toEqual(['marked-before-reload'])
  })

  it('does not mark the draft when publish fails validation or rejects', async () => {
    const markDraftPublished = vi.fn()
    mocks.publishWorkflowDraft.mockResolvedValueOnce({
      valid: false,
      errors: ['missing key'],
    })
    const { result } = renderHook(() =>
      useWorkflowStudioActions(
        'ws1',
        { ...draft, markDraftPublished },
        reload,
        compare
      )
    )

    await act(async () => {
      await result.current.publishDraft()
    })
    expect(markDraftPublished).not.toHaveBeenCalled()

    mocks.publishWorkflowDraft.mockRejectedValueOnce(new Error('network error'))
    await act(async () => {
      await result.current.publishDraft()
    })
    expect(markDraftPublished).not.toHaveBeenCalled()
  })
})
