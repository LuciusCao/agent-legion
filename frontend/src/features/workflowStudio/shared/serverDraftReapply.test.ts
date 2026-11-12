import { describe, expect, it, vi } from 'vitest'
import { ServerDraftApplyTracker } from './serverDraftReapply'
import type { ServerDraftReapplyInput } from './useServerDraftApply.types'

/** 重应用跟踪器的状态用例（纯决策见 serverDraftAdvance.test.ts）。 */

const BASE_AT = '2026-08-27T01:00:00+00:00'
const NEXT_AT = '2026-08-27T02:00:00+00:00'

function input(
  overrides: Partial<ServerDraftReapplyInput> = {}
): ServerDraftReapplyInput {
  return {
    serverDraftYaml: 'key: demo\n',
    serverDraftUpdatedAt: BASE_AT,
    setDraftYamlState: () => {},
    ...overrides,
  }
}

describe('ServerDraftApplyTracker', () => {
  it('an applied own-save echo resets touched — the next different agent update is not a phantom conflict', () => {
    const tracker = new ServerDraftApplyTracker()
    tracker.markTouched()
    // own-save 回显：服务端草稿 = 画布内容 → apply（确认基线），touched 清零。
    const applied = tracker.evaluate(
      input({
        serverDraftYaml: 'key: demo\nlabel: Mine\n',
        serverDraftUpdatedAt: NEXT_AT,
        canvasYaml: 'key: demo\nlabel: Mine\n',
      })
    )
    expect(applied).toBe('apply')
    // touched 已清：内容不同的下一个 agent 更新走 apply，不再幻影冲突。
    const next = tracker.evaluate(
      input({
        serverDraftYaml: 'key: demo\nlabel: Agent v2\n',
        serverDraftUpdatedAt: '2026-08-27T03:00:00+00:00',
        canvasYaml: 'key: demo\nlabel: Mine\n',
      })
    )
    expect(next).toBe('apply')
  })

  it('records the applied generation and judges the next update by it', () => {
    /* 更高代次带相同 updated_at：tracker 按代次判定前进，不得 no-op。 */
    const tracker = new ServerDraftApplyTracker()
    expect(tracker.evaluate(input({ serverDraftGeneration: 7 }))).toBe('apply')
    expect(
      tracker.evaluate(
        input({
          serverDraftYaml: 'key: demo\nlabel: Agent v2\n',
          serverDraftGeneration: 8,
        })
      )
    ).toBe('apply')
  })

  it('distinguishes stale, conflicting, adopted and locally retained drafts', () => {
    const tracker = new ServerDraftApplyTracker()
    const apply = vi.fn()
    const local = 'key: demo\nlabel: Mine\n'
    const agent = 'key: demo\nlabel: Agent v2\n'
    expect(tracker.evaluate(input({ serverDraftGeneration: 7 }))).toBe('apply')
    tracker.markTouched()

    // 乱序旧代次即使时间戳更晚也保持本地内容，且不制造冲突。
    expect(
      tracker.evaluate(
        input({
          serverDraftYaml: agent,
          serverDraftUpdatedAt: '2026-08-27T04:00:00+00:00',
          serverDraftGeneration: 6,
          setDraftYamlState: apply,
          canvasYaml: local,
        })
      )
    ).toBe('noop')
    expect(apply).not.toHaveBeenCalled()
    expect(tracker.consumeConflict()).toBeNull()

    // 真正前进的外部草稿与本地编辑冲突：不写画布，通知携带 CAS 身份。
    expect(
      tracker.evaluate(
        input({
          serverDraftYaml: agent,
          serverDraftUpdatedAt: NEXT_AT,
          serverDraftHash: 'agent-hash',
          serverDraftGeneration: 8,
          setDraftYamlState: apply,
          canvasYaml: local,
        })
      )
    ).toBe('conflict')
    expect(apply).not.toHaveBeenCalled()
    expect(tracker.consumeConflict()).toEqual({
      yaml: agent,
      updatedAt: NEXT_AT,
      hash: 'agent-hash',
      generation: 8,
    })

    // 用户采用后画布等于服务端草稿：推进已应用代次并清 touched；后续
    // 不同 agent 更新直接应用，不被误报成幻影冲突。
    expect(
      tracker.evaluate(
        input({
          serverDraftYaml: agent,
          serverDraftUpdatedAt: NEXT_AT,
          serverDraftGeneration: 8,
          setDraftYamlState: apply,
          canvasYaml: agent,
        })
      )
    ).toBe('apply')
    expect(apply).toHaveBeenCalledWith(agent)
    expect(
      tracker.evaluate(
        input({
          serverDraftYaml: 'key: demo\nlabel: Agent v3\n',
          serverDraftUpdatedAt: '2026-08-27T03:00:00+00:00',
          serverDraftGeneration: 9,
          canvasYaml: agent,
        })
      )
    ).toBe('apply')
  })
})
