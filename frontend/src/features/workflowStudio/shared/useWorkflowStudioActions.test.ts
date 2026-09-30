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
    expect(result.current.actionState).toBe('idle')
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

  it('校验请求网络错误：校验失败：原因', async () => {
    mocks.validateWorkflowDraft.mockRejectedValue(new Error('network error'))
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    expect(result.current.validationMessage).toBe('校验失败：network error')
    expect(result.current.validationErrors).toEqual([])
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
    expect(result.current.actionState).toBe('validating')

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
    expect(result.current.actionState).toBe('idle')
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

    expect(result.current.actionState).toBe('idle')
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
