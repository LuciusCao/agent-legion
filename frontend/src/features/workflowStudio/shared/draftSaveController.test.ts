import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { DraftSaveController } from './draftSaveController'
import type { PutWorkflowDraftFn } from './draftSaveTypes'

/* #1226 四轮 codex P1 的机制钉：hydrate 落地的新持久化基线可能正是挂起
 * debounce 的内容（发布后画布被 reset 到 canonical、尾随 schedule 已
 * arm）——该保存已成为 no-op，hydrate 必须取消它，否则到期的 PUT 会按
 * CAS-over-absence 语义立刻重建刚删除的草稿行；内容不同的挂起保存
 * （用户在窗口内的真实编辑）不受影响。 */

function makeController() {
  const put = vi.fn<PutWorkflowDraftFn>(() =>
    Promise.resolve({
      definition_yaml: null,
      updated_at: '2026-08-27T00:00:00+00:00',
      definition_hash: null,
      generation: 2,
    })
  )
  return { put, controller: new DraftSaveController(put) }
}

describe('DraftSaveController hydrate vs pending save', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('cancels a pending debounce whose content equals the new baseline', () => {
    const { put, controller } = makeController()
    controller.hydrate('baseline-v1', '2026-08-26T00:00:00+00:00', 1)
    controller.schedule('canonical')
    expect(controller.hasUnsaved()).toBe(true)

    controller.hydrate('canonical', null, 2)

    expect(controller.hasUnsaved()).toBe(false)
    vi.advanceTimersByTime(2000)
    expect(put).not.toHaveBeenCalled()
  })

  it('keeps a pending save whose content differs from the new baseline', () => {
    const { put, controller } = makeController()
    controller.hydrate('baseline-v1', '2026-08-26T00:00:00+00:00', 1)
    controller.schedule('user edit')

    controller.hydrate('canonical', null, 2)

    expect(controller.hasUnsaved()).toBe(true)
    vi.advanceTimersByTime(2000)
    expect(put).toHaveBeenCalledTimes(1)
    expect(put.mock.calls[0]?.[0]).toBe('user edit')
  })
})
