import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { DraftSaveController } from './draftSaveController'
import { isBlankDraftYaml } from './draftSaveConflict'
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

/* #1196：前后端空白判据对齐——后端 _not_blank 用 Python str.strip()
 * （空白全集含 \u0085/\u001C-\u001F，JS trim() 不剥）；isBlankDraftYaml
 * 是 PUT 门的唯一口径（decideSchedule 与 forceSave 护栏共用），判空集
 * ⊇ 后端判空集（多 \uFEFF：JS 剥、Python 不剥——skip 方向安全，永不
 * 发出服务端必以 422 拒绝的内容）。 */
describe('isBlankDraftYaml 前后端空白判据对齐（#1196）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  // 两侧都判空白（Python strip 全集，含 JS trim 不剥的 NEL/FS/GS/RS/US）。
  const bothBlank = [
    '',
    '   \n  ',
    '\u0085',
    '\u001C',
    '\u001D\u001E\u001F',
    '\u00A0',
    '\u3000',
  ]
  // 仅前端判空白（\uFEFF：JS 剥、Python 不剥）——skip 的安全方向。
  const frontendOnlyBlank = ['\uFEFF']
  // 两侧都非空白（\u007F DEL 两侧都不剥——前端发、服务端收）。
  const nonBlank = ['key: wf', '  key: wf  ', '\u007F']

  it.each([...bothBlank, ...frontendOnlyBlank])(
    'treats %j as blank',
    (yaml) => {
      expect(isBlankDraftYaml(yaml)).toBe(true)
    }
  )
  it.each(nonBlank)('treats %j as non-blank', (yaml) => {
    expect(isBlankDraftYaml(yaml)).toBe(false)
  })

  it('schedule 对 Python 侧空白直接 skip：不发起 PUT、不留 settled', () => {
    const { put, controller } = makeController()
    let status: string | undefined
    controller.subscribe((s) => {
      status = s.status
    })
    controller.hydrate('baseline-v1', '2026-08-26T00:00:00+00:00', 1)

    controller.schedule('\u0085\r\n\u001C')
    vi.advanceTimersByTime(2000)

    expect(put).not.toHaveBeenCalled()
    // 空白与任何已落盘内容都不同——status 必须离开 settled（#1177 V1 不变量）。
    expect(status).toBe('pending')
  })
})
