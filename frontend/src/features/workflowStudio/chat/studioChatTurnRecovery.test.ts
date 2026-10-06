import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './studioChatMessages'
import {
  emptyTurnRetryPending,
  queuedMessageStates,
} from './studioChatTurnRecovery'

let seq = 0
function msg(
  kind: string,
  role: string,
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
  } as ChatMessage
}
const user = (text: string, extra: Record<string, unknown> = {}, id?: string) =>
  msg('text', 'user', { text, ...extra }, id)
const status = (event: string, extra: Record<string, unknown> = {}) =>
  msg('status', 'system', { event, ...extra })

describe('emptyTurnRetryPending (#882)', () => {
  it('is pending right after a confirmed empty turn', () => {
    const messages = [
      user('hi', {}, 'u1'),
      status('turn_end'),
      status('empty_turn', { message_id: 'u1' }),
    ]
    expect(emptyTurnRetryPending(messages)).toBe(true)
  })

  it('skips unrelated status rows after the verdict', () => {
    const messages = [
      user('hi', {}, 'u1'),
      status('turn_end'),
      status('empty_turn', { message_id: 'u1' }),
      status('mcp_unverified'),
    ]
    expect(emptyTurnRetryPending(messages)).toBe(true)
  })

  it.each([
    ['a replay', status('empty_turn_retry', { message_id: 'u1' })],
    ['a newer turn', status('turn_end')],
    ['a newer user message', user('again')],
    ['agent content', msg('text', 'agent', { text: 'late' })],
  ])('is cleared by %s', (_label, later) => {
    const messages = [
      user('hi', {}, 'u1'),
      status('empty_turn', { message_id: 'u1' }),
      later,
    ]
    expect(emptyTurnRetryPending(messages)).toBe(false)
  })

  it('ignores legacy verdicts without a message id', () => {
    expect(emptyTurnRetryPending([user('hi'), status('empty_turn')])).toBe(
      false
    )
  })
})

describe('queuedMessageStates (#882)', () => {
  it('marks queued messages pending until delivered', () => {
    const messages = [
      user('a', { queued: true }, 'q1'),
      user('b', { queued: true }, 'q2'),
      status('queued_delivered', { message_id: 'q1' }),
    ]
    const states = queuedMessageStates(messages, true)
    expect(states.has('q1')).toBe(false)
    expect(states.get('q2')).toBe('pending')
  })

  it('marks dropped and orphaned queued messages undelivered', () => {
    const dropped = queuedMessageStates(
      [
        user('a', { queued: true }, 'q1'),
        status('queued_dropped', { message_id: 'q1' }),
      ],
      true
    )
    expect(dropped.get('q1')).toBe('dropped')
    const resumed = queuedMessageStates(
      [user('a', { queued: true }, 'q1'), status('session_resumed')],
      true
    )
    expect(resumed.get('q1')).toBe('dropped')
    const dead = queuedMessageStates([user('a', { queued: true }, 'q1')], false)
    expect(dead.get('q1')).toBe('dropped')
  })

  it('leaves ordinary user messages unmarked', () => {
    expect(queuedMessageStates([user('plain')], true).size).toBe(0)
  })
})
