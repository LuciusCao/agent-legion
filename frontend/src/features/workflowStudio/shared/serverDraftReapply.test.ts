import { describe, expect, it } from 'vitest'
import {
  decideServerDraftReapply,
  isServerDraftNewer,
} from './serverDraftReapply'

/** #633 codex review P1-2：重应用决策的纯函数用例。 */

describe('isServerDraftNewer', () => {
  it('treats any draft as newer when nothing was applied yet', () => {
    expect(isServerDraftNewer('2026-08-27T01:02:03+00:00', null)).toBe(true)
    expect(isServerDraftNewer('2026-08-27T01:02:03+00:00', undefined)).toBe(
      true
    )
  })

  it('compares by time value across offset renderings', () => {
    // Postgres `+00`、Python `+00:00` 与 `Z` 渲染同一时刻：不算更新。
    expect(
      isServerDraftNewer('2026-08-27T01:02:03+00', '2026-08-27T01:02:03+00:00')
    ).toBe(false)
    expect(
      isServerDraftNewer('2026-08-27T01:02:03Z', '2026-08-27T01:02:03+00:00')
    ).toBe(false)
    expect(
      isServerDraftNewer('2026-08-27T01:02:04+00', '2026-08-27T01:02:03+00:00')
    ).toBe(true)
    // 迟到的旧响应不算新（乱序到达不回退画布）。
    expect(
      isServerDraftNewer(
        '2026-08-27T00:00:00+00:00',
        '2026-08-27T01:02:03+00:00'
      )
    ).toBe(false)
  })

  it('rejects null candidates and unparseable values', () => {
    expect(isServerDraftNewer(null, '2026-08-27T01:02:03+00:00')).toBe(false)
    expect(isServerDraftNewer(undefined, '2026-08-27T01:02:03+00:00')).toBe(
      false
    )
    expect(isServerDraftNewer('garbage', '2026-08-27T01:02:03+00:00')).toBe(
      false
    )
  })
})

describe('decideServerDraftReapply', () => {
  it('is a no-op while the query has not resolved', () => {
    expect(
      decideServerDraftReapply({
        serverDraftYaml: undefined,
        serverDraftUpdatedAt: undefined,
        appliedUpdatedAt: null,
        userTouched: false,
      })
    ).toEqual({ action: 'noop', reason: 'not-ready' })
    expect(
      decideServerDraftReapply({
        serverDraftYaml: null,
        serverDraftUpdatedAt: null,
        appliedUpdatedAt: null,
        userTouched: false,
      })
    ).toEqual({ action: 'noop', reason: 'not-ready' })
  })

  it('is a no-op when the updated_at did not advance', () => {
    expect(
      decideServerDraftReapply({
        serverDraftYaml: 'key: demo\n',
        serverDraftUpdatedAt: '2026-08-27T01:02:03+00:00',
        appliedUpdatedAt: '2026-08-27T01:02:03+00',
        userTouched: false,
      })
    ).toEqual({ action: 'noop', reason: 'same-or-older' })
  })

  it('applies when the server draft advanced and the user is untouched', () => {
    expect(
      decideServerDraftReapply({
        serverDraftYaml: 'key: demo\nlabel: Agent v2\n',
        serverDraftUpdatedAt: '2026-08-27T02:00:00+00:00',
        appliedUpdatedAt: '2026-08-27T01:02:03+00:00',
        userTouched: false,
      })
    ).toEqual({
      action: 'apply',
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T02:00:00+00:00',
    })
  })

  it('conflicts (preserving edits) when the user touched the draft', () => {
    expect(
      decideServerDraftReapply({
        serverDraftYaml: 'key: demo\nlabel: Agent v2\n',
        serverDraftUpdatedAt: '2026-08-27T02:00:00+00:00',
        appliedUpdatedAt: '2026-08-27T01:02:03+00:00',
        userTouched: true,
      })
    ).toEqual({
      action: 'conflict',
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T02:00:00+00:00',
    })
  })

  it('treats an own-save echo as an apply, not a phantom conflict (kimi review P1-1)', () => {
    // 用户自己保存成功后 turn-end 重取：服务端草稿 === 画布当前内容——
    // 即使 touched=true 也不得误报「其它会话更新」；静默推进基线。
    expect(
      decideServerDraftReapply({
        serverDraftYaml: 'key: demo\nlabel: Mine\n',
        serverDraftUpdatedAt: '2026-08-27T02:00:00+00:00',
        appliedUpdatedAt: '2026-08-27T01:02:03+00:00',
        userTouched: true,
        canvasYaml: 'key: demo\nlabel: Mine\n',
      })
    ).toEqual({
      action: 'apply',
      yaml: 'key: demo\nlabel: Mine\n',
      updatedAt: '2026-08-27T02:00:00+00:00',
    })
    // 内容不同（真正的外部变更）仍走 conflict。
    expect(
      decideServerDraftReapply({
        serverDraftYaml: 'key: demo\nlabel: Agent v2\n',
        serverDraftUpdatedAt: '2026-08-27T02:00:00+00:00',
        appliedUpdatedAt: '2026-08-27T01:02:03+00:00',
        userTouched: true,
        canvasYaml: 'key: demo\nlabel: Mine\n',
      })
    ).toEqual({
      action: 'conflict',
      yaml: 'key: demo\nlabel: Agent v2\n',
      updatedAt: '2026-08-27T02:00:00+00:00',
    })
  })
})
