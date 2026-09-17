import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './studioChatMessages'
import { supersededCancelRequestIds } from './studioChatCancelVisibility'

let seq = 0
function message(
  kind: ChatMessage['kind'],
  role: ChatMessage['role'],
  content: Record<string, unknown>,
  id?: string
): ChatMessage {
  seq += 1
  return {
    id: id ?? `m${seq}`,
    session_id: 's1',
    kind,
    role,
    content,
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('supersededCancelRequestIds', () => {
  /** #675 codex P2：等待语义的了结判定——只有终止事件或新一轮能让
   * cancel_requested 行翻篇，收尾窗口内的行不能。 */
  const cancelRequested = () =>
    message('status', 'system', { event: 'cancel_requested' })
  const turnEnd = (stopReason: string) =>
    message('status', 'system', { event: 'turn_end', stop_reason: stopReason })

  it('keeps the request awaiting while only wind-down rows follow', () => {
    const cancel = cancelRequested()
    const ids = supersededCancelRequestIds([
      cancel,
      message('thought', 'agent', { text: '整理已有结果' }),
      message('text', 'agent', { text: '已完成的部分如下' }),
      message('tool_call', 'agent', {
        sessionUpdate: 'tool_call',
        toolCallId: 'tc1',
        title: 'Agent',
        status: 'in_progress',
      }),
    ])
    expect(ids.has(cancel.id)).toBe(false)
  })

  it('supersedes once the cancelled turn_end arrives', () => {
    const cancel = cancelRequested()
    const ids = supersededCancelRequestIds([cancel, turnEnd('cancelled')])
    expect(ids.has(cancel.id)).toBe(true)
  })

  it('supersedes on a normally-ended turn as well', () => {
    const cancel = cancelRequested()
    expect(
      supersededCancelRequestIds([cancel, turnEnd('end_turn')]).has(cancel.id)
    ).toBe(true)
  })

  it('supersedes on other terminal statuses (error / closed / resumed)', () => {
    for (const event of ['error', 'session_closed', 'session_resumed']) {
      const cancel = cancelRequested()
      const ids = supersededCancelRequestIds([
        cancel,
        message('status', 'system', { event }),
      ])
      expect(ids.has(cancel.id)).toBe(true)
    }
  })

  it('supersedes when a newer turn starts even without turn_end', () => {
    // 后端重启丢失 turn_end 的兜底：新一轮用户消息同样让旧行翻篇。
    const cancel = cancelRequested()
    const ids = supersededCancelRequestIds([
      cancel,
      message('text', 'user', { text: '继续把结果说完' }),
    ])
    expect(ids.has(cancel.id)).toBe(true)
  })

  it('keeps a later turn fresh request awaiting while the old one is history', () => {
    const first = cancelRequested()
    const second = cancelRequested()
    const ids = supersededCancelRequestIds([
      first,
      turnEnd('cancelled'),
      second,
    ])
    expect(ids.has(first.id)).toBe(true)
    expect(ids.has(second.id)).toBe(false)
  })

  it('supersedes duplicate requests within the same turn together', () => {
    const first = cancelRequested()
    const second = cancelRequested()
    const ids = supersededCancelRequestIds([
      first,
      second,
      turnEnd('cancelled'),
    ])
    expect(ids.has(first.id)).toBe(true)
    expect(ids.has(second.id)).toBe(true)
  })

  it('returns an empty set without any cancel request', () => {
    expect(supersededCancelRequestIds([])).toEqual(new Set())
    expect(supersededCancelRequestIds([turnEnd('end_turn')])).toEqual(new Set())
  })
})
