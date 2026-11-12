import { renderHook, waitFor } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { VALIDATION_TRIGGER_DELAY_MS } from './draftAutoValidationRunner'
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
  invalidateServerDraft: () => Promise<unknown>
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
  markRuntimeChannelSaved: vi.fn(),
  invalidateServerDraft: vi.fn(() => Promise.resolve()),
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

const compare: UseWorkflowDraftCompareResult = {
  compareState: 'idle',
  compareResponse: null,
  compareErrors: null,
  compareSummary: null,
  retry: vi.fn(),
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
  retry: vi.fn(),
}

const reload = vi.fn().mockResolvedValue(undefined)

type AutoProps = {
  saveStatus: DraftSaveStatus
  definitionYaml: string
  canSubmit?: boolean
  conflict?: boolean
}

function renderActionsHook(initial: AutoProps) {
  return renderHook(
    ({ saveStatus, definitionYaml, canSubmit, conflict }: AutoProps) =>
      useWorkflowStudioActions(
        'ws1',
        {
          ...draft,
          definitionYaml,
          canSubmit: canSubmit ?? true,
          draftSave: { status: saveStatus, savedAt: null, conflict },
        },
        reload,
        compare
      ),
    { initialProps: initial }
  )
}

/** #1360：校验触发有一拍延迟——等信号非等时长（时序纪律，codex P1）：
 * fake timers 直接推进触发窗口；real timers 轮询「validateWorkflowDraft
 * 调用数」这一可观察信号（调用即触发完成），随后冲刷落定 microtask 让
 * 后续断言读到最新 state。 */
async function advanceValidationTrigger(callCount = 1) {
  if (vi.isFakeTimers()) {
    await act(async () => {
      vi.advanceTimersByTime(VALIDATION_TRIGGER_DELAY_MS)
    })
    return
  }
  await waitFor(
    () => expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(callCount),
    { timeout: 4000 }
  )
  await act(async () => {})
}

describe('useWorkflowStudioActions（发布）', () => {
  beforeEach(() => {
    useUiStore.setState({ toast: null })
    vi.clearAllMocks()
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
  })

  it('发布门控绑定当前 YAML：校验通过前 canPublish=false，通过后 true（codex 轮 3 P2）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml, conflict }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null, conflict },
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
    await advanceValidationTrigger()
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)
  })

  it('冲突态禁发布：canPublish 不含 conflict（轮 6 H2：冲突未解决不得隐式 keep-mine 发布）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml, conflict }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null, conflict },
          },
          reload,
          compareWithChanges
        ),
      { initialProps: { saveStatus: 'idle', definitionYaml: 'key: demo\n' } }
    )
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    await advanceValidationTrigger()
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)

    // 校验通过后 agent 保存 D2 → 409 冲突（definitionYaml 未变、message
    // 不清——旧实现此处 canPublish 仍 true，revert 即红）。
    await act(async () => {
      rerender({
        saveStatus: 'saved',
        definitionYaml: 'key: demo\n',
        conflict: true,
      })
    })
    expect(result.current.canPublish).toBe(false)
  })

  it('确认框打开后进入 conflict（YAML 不变）→ 审阅失效 reviewStale（轮 7 P1：绕过冲突门控的洞）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml, conflict }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null, conflict },
          },
          reload,
          compareWithChanges
        ),
      { initialProps: { saveStatus: 'idle', definitionYaml: 'key: demo\n' } }
    )
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    await advanceValidationTrigger()
    expect(result.current.canPublish).toBe(true)
    act(() => result.current.requestPublish())
    expect(result.current.reviewDialogOpen).toBe(true)
    expect(result.current.reviewStale).toBe(false)

    // Agent 推进服务端草稿 → 冲突处理保留画布 YAML、只置 conflict 标记。
    // reviewStale 只比 YAML 的旧实现此处仍 false（确认键可用=绕过冲突门控）。
    await act(async () => {
      rerender({
        saveStatus: 'error',
        definitionYaml: 'key: demo\n',
        conflict: true,
      })
    })
    expect(result.current.reviewStale).toBe(true)
    expect(result.current.canPublish).toBe(false)
  })

  it('发布失败不污染校验通道：validationMessage 保持「校验通过」，可重发（轮 7 P1）', async () => {
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
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    await advanceValidationTrigger()
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)

    // 瞬时网络错误 reject：旧实现把 validationMessage 覆盖成「保存失败：…」
    // → canPublish 永假、自动校验因消息非空跳过（发布永久封死）。
    mocks.publishWorkflowDraft.mockRejectedValueOnce(new Error('network error'))
    await act(async () => {
      await result.current.publishDraft()
    })
    expect(result.current.publishing).toBe(false)
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)
    // toast 仍报失败（用户可见）。
    expect(useUiStore.getState().toast).toEqual({
      message: '保存失败：network error',
      type: 'error',
    })
  })

  it('审 A 发 B：确认框打开期间 YAML 被后台换掉 → reviewStale（禁确认数据源），关闭复位（轮 4 P2-C）', async () => {
    const { result, rerender } = renderHook(
      ({ saveStatus, definitionYaml, conflict }: AutoProps) =>
        useWorkflowStudioActions(
          'ws1',
          {
            ...draft,
            definitionYaml,
            draftSave: { status: saveStatus, savedAt: null, conflict },
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
    await advanceValidationTrigger()
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

  it('publish 网络失败只 toast 不写校验通道（轮 7 P1：校验通过不被污染）', async () => {
    mocks.publishWorkflowDraft.mockRejectedValue(new Error('network error'))
    const { result } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await act(async () => {
      await result.current.publishDraft()
    })

    expect(result.current.publishing).toBe(false)
    expect(useUiStore.getState().toast).toEqual({
      message: '保存失败：network error',
      type: 'error',
    })
    // 校验通道不受影响（initial '' 保持）。
    expect(result.current.validationMessage).toBe('')
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
