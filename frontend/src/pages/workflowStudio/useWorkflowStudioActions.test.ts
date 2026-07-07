import { renderHook } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import type { UseWorkflowStudioDraftResult } from './useWorkflowStudioDraft'
import type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare'

const mocks = {
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../api', () => ({
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
    expect(result.current.validationMessage).toBe('校验失败：网络错误')
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
    expect(result.current.validationMessage).toBe('发布失败：网络错误')
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
})
