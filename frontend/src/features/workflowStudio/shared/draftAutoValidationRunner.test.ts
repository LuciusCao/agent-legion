/* #1359（#1196 同族）：4xx 确定性拒绝不退避重试；5xx/网络错误保持
 * 2s/4s/8s 指数退避至多 3 次。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DraftValidationStaleError,
  MAX_TRANSPORT_RETRIES,
  validateDraftWithRetry,
} from './draftAutoValidationRunner'

const mocks = {
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../../api', () => ({
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
}))

function httpError(status: number): Error {
  return Object.assign(new Error(`HTTP ${status}`), { status })
}

describe('validateDraftWithRetry', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.validateWorkflowDraft.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('validate resolve 直接返回不重试', async () => {
    mocks.validateWorkflowDraft.mockResolvedValue({
      valid: false,
      errors: ['x'],
    })
    const result = await validateDraftWithRetry('ws1', 'yaml', () => false)
    expect(result).toEqual({ valid: false, errors: ['x'] })
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
  })

  it('#1359：4xx 确定性拒绝立即终结，不退避重试', async () => {
    mocks.validateWorkflowDraft.mockRejectedValue(httpError(422))
    await expect(
      validateDraftWithRetry('ws1', 'yaml', () => false)
    ).rejects.toMatchObject({ status: 422 })
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
  })

  it('#1359：401/404 同样立即终结', async () => {
    for (const status of [401, 404]) {
      mocks.validateWorkflowDraft.mockReset()
      mocks.validateWorkflowDraft.mockRejectedValue(httpError(status))
      await expect(
        validateDraftWithRetry('ws1', 'yaml', () => false)
      ).rejects.toMatchObject({ status })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
    }
  })

  it('5xx 保持退避重试，耗尽后抛最后一次错误', async () => {
    mocks.validateWorkflowDraft.mockRejectedValue(httpError(500))
    const promise = validateDraftWithRetry('ws1', 'yaml', () => false)
    const assertion = expect(promise).rejects.toMatchObject({ status: 500 })
    await vi.advanceTimersByTimeAsync(2000 + 4000 + 8000)
    await assertion
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(
      MAX_TRANSPORT_RETRIES + 1
    )
  })

  it('网络错误（无 status）保持退避重试', async () => {
    mocks.validateWorkflowDraft.mockRejectedValue(new Error('network down'))
    const promise = validateDraftWithRetry('ws1', 'yaml', () => false)
    const assertion = expect(promise).rejects.toThrow('network down')
    await vi.advanceTimersByTimeAsync(2000 + 4000 + 8000)
    await assertion
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(
      MAX_TRANSPORT_RETRIES + 1
    )
  })

  it('退避期间内容已变则提前中止（StaleError）', async () => {
    let stale = false
    mocks.validateWorkflowDraft.mockRejectedValue(new Error('network down'))
    const promise = validateDraftWithRetry('ws1', 'yaml', () => stale)
    const assertion = expect(promise).rejects.toBeInstanceOf(
      DraftValidationStaleError
    )
    await vi.advanceTimersByTimeAsync(0)
    stale = true
    await vi.advanceTimersByTimeAsync(2000)
    await assertion
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
  })
})
