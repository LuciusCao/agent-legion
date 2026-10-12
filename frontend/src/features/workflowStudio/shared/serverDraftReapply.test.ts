import { describe, expect, it } from 'vitest'
import { ServerDraftApplyTracker } from './serverDraftReapply'

/** #633 codex review P1-2：重应用跟踪器的状态用例（纯决策见
 * serverDraftAdvance.test.ts）。 */

describe('ServerDraftApplyTracker (codex R4 P2: apply clears touched)', () => {
  it('an applied own-save echo resets touched — the next different agent update is not a phantom conflict', () => {
    const tracker = new ServerDraftApplyTracker()
    tracker.markTouched()
    // own-save 回显：服务端草稿 = 画布内容 → apply（确认基线），touched 清零。
    const applied = tracker.evaluate(
      'key: demo\nlabel: Mine\n',
      '2026-08-27T02:00:00+00:00',
      () => {},
      'key: demo\nlabel: Mine\n'
    )
    expect(applied).toBe('apply')
    // touched 已清：内容不同的下一个 agent 更新走 apply，不再幻影冲突。
    const next = tracker.evaluate(
      'key: demo\nlabel: Agent v2\n',
      '2026-08-27T03:00:00+00:00',
      () => {},
      'key: demo\nlabel: Mine\n'
    )
    expect(next).toBe('apply')
  })

  it('records the applied generation and judges the next update by it (#1226 复审 P2)', () => {
    /* 更高代次带相同 updated_at（并发事务的 current_timestamp 同值）——
       tracker 按代次判定前进，不得 no-op 掉新草稿。 */
    const tracker = new ServerDraftApplyTracker()
    const first = tracker.evaluate(
      'key: demo\n',
      '2026-08-27T02:00:00+00:00',
      () => {},
      undefined,
      null,
      7
    )
    expect(first).toBe('apply')
    const second = tracker.evaluate(
      'key: demo\nlabel: Agent v2\n',
      '2026-08-27T02:00:00+00:00',
      () => {},
      undefined,
      null,
      8
    )
    expect(second).toBe('apply')
  })
})
