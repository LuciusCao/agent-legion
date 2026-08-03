import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { upgradeJobWorkflow } from '../../api/jobWorkflowUpgradeApi'
import { useUpgradeWorkflowAction } from './useUpgradeWorkflowAction'

vi.mock('../../api/jobWorkflowUpgradeApi', () => ({
  upgradeJobWorkflow: vi.fn(),
}))

const mockUpgrade = vi.mocked(upgradeJobWorkflow)

beforeEach(() => {
  vi.clearAllMocks()
})

function setup(jobId: string | undefined) {
  const refreshDetail = vi.fn().mockResolvedValue(null)
  const setActionLoading = vi.fn()
  const setError = vi.fn()
  const { result } = renderHook(() =>
    useUpgradeWorkflowAction(jobId, refreshDetail, setActionLoading, setError)
  )
  return { result, refreshDetail, setActionLoading, setError }
}

describe('useUpgradeWorkflowAction', () => {
  it('upgrades, refreshes the detail, and toggles loading around the call', async () => {
    mockUpgrade.mockResolvedValue({
      job_id: 'job-1',
      operation: 'upgrade_workflow',
      status: 'succeeded',
      node_key: null,
      reason_code: null,
      message: null,
    })
    const { result, refreshDetail, setActionLoading, setError } = setup('job-1')

    await act(() => result.current())

    expect(mockUpgrade).toHaveBeenCalledWith('job-1')
    expect(refreshDetail).toHaveBeenCalledTimes(1)
    expect(setActionLoading.mock.calls).toEqual([[true], [false]])
    expect(setError).not.toHaveBeenCalled()
  })

  it('surfaces request failures and still clears loading', async () => {
    mockUpgrade.mockRejectedValue(new Error('Job is already current'))
    const { result, refreshDetail, setActionLoading, setError } = setup('job-1')

    await act(() => result.current())

    expect(setError).toHaveBeenCalledWith('Job is already current')
    expect(refreshDetail).not.toHaveBeenCalled()
    expect(setActionLoading.mock.calls).toEqual([[true], [false]])
  })

  it('stringifies non-error rejections', async () => {
    mockUpgrade.mockRejectedValue('plain failure')
    const { result, setError } = setup('job-1')

    await act(() => result.current())

    expect(setError).toHaveBeenCalledWith('plain failure')
  })

  it('does nothing without a job id', async () => {
    const { result, refreshDetail, setActionLoading, setError } =
      setup(undefined)

    await act(() => result.current())

    expect(mockUpgrade).not.toHaveBeenCalled()
    expect(refreshDetail).not.toHaveBeenCalled()
    expect(setActionLoading).not.toHaveBeenCalled()
    expect(setError).not.toHaveBeenCalled()
  })
})
