import { renderHook } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { useUiStore } from '../../../stores/uiStore'
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

const draft: UseWorkflowStudioDraftResult = {
  draftYaml: 'key: demo\n',
  setDraftYaml: vi.fn(),
  definitionYaml: 'key: demo\n',
  visibleWorkflow: null,
  visibleRevision: null,
  readOnly: false,
  dirty: false,
  canSubmit: true,
  viewMode: 'draft',
  selectedRevisionId: null,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
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

describe('useWorkflowStudioActions', () => {
  beforeEach(() => {
    useUiStore.setState({ toast: null })
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
  })

  it('sets validation failure message and clears errors on validate rejection', async () => {
    mocks.validateWorkflowDraft.mockRejectedValue(new Error('network error'))
    const { result } = renderHook(() =>
      useWorkflowStudioActions('ws1', draft, reload, compare)
    )

    await act(async () => {
      await result.current.validateDraft()
    })

    expect(result.current.actionState).toBe('idle')
    expect(result.current.validationMessage).toBe('校验失败：network error')
    expect(result.current.validationErrors).toEqual([])
  })

  it('sets validation failure message and clears errors on publish rejection', async () => {
    mocks.publishWorkflowDraft.mockRejectedValue(new Error('network error'))
    const { result } = renderHook(() =>
      useWorkflowStudioActions('ws1', draft, reload, compare)
    )

    await act(async () => {
      await result.current.publishDraft()
    })

    expect(result.current.actionState).toBe('idle')
    expect(result.current.validationMessage).toBe('保存失败：network error')
    expect(result.current.validationErrors).toEqual([])
  })

  it('still shows validation errors returned by the API', async () => {
    mocks.validateWorkflowDraft.mockResolvedValue({
      valid: false,
      errors: ['missing key'],
    })
    const { result } = renderHook(() =>
      useWorkflowStudioActions('ws1', draft, reload, compare)
    )

    await act(async () => {
      await result.current.validateDraft()
    })

    expect(result.current.validationMessage).toBe('校验失败')
    expect(result.current.validationErrors).toEqual(['missing key'])
  })

  it('toasts publish/validate outcomes (feedback visible when the changes view is hidden)', async () => {
    const { result } = renderHook(() =>
      useWorkflowStudioActions('ws1', draft, reload, compare)
    )

    await act(async () => {
      await result.current.validateDraft()
    })
    expect(useUiStore.getState().toast).toEqual({
      message: '校验通过',
      type: 'success',
    })

    await act(async () => {
      await result.current.publishDraft()
    })
    expect(useUiStore.getState().toast).toEqual({
      message: '保存成功',
      type: 'success',
    })
  })

  it('clears stale validation state when the draft is edited again', async () => {
    const { result, rerender } = renderHook(
      ({ definitionYaml }) =>
        useWorkflowStudioActions(
          'ws1',
          { ...draft, definitionYaml },
          reload,
          compare
        ),
      { initialProps: { definitionYaml: 'key: demo\n' } }
    )

    await act(async () => {
      await result.current.validateDraft()
    })
    expect(result.current.validationMessage).toBe('校验通过')

    rerender({ definitionYaml: 'key: demo\nlabel: changed\n' })

    expect(result.current.validationMessage).toBe('')
    expect(result.current.validationErrors).toEqual([])
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
