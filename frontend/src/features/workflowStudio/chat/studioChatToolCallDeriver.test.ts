import { describe, expect, it } from 'vitest'
import {
  groupToolCalls,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import { createToolCallDeriver } from './studioChatToolCallDeriver'

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

function toolCall(
  toolCallId: string,
  update: Record<string, unknown>,
  id?: string
): ChatMessage {
  return message(
    'tool_call',
    'agent',
    { sessionUpdate: 'tool_call', toolCallId, ...update },
    id
  )
}

function toolCallUpdate(
  toolCallId: string,
  update: Record<string, unknown>,
  id?: string
): ChatMessage {
  const m = toolCall(toolCallId, update, id)
  m.content = { ...m.content, sessionUpdate: 'tool_call_update' }
  return m
}

describe('createToolCallDeriver（#1120 增量归并）', () => {
  it('derives the same views as groupToolCalls, including multi-message merges', () => {
    const messages = [
      toolCall('t1', { title: 'list_workflows', status: 'pending' }),
      toolCallUpdate('t1', { status: 'in_progress' }),
      toolCall('t2', {
        title: 'validate_workflow',
        status: 'completed',
        rawInput: { definition_yaml: 'key: x\n' },
      }),
      toolCallUpdate('t1', {
        status: 'completed',
        rawOutput: { content: [{ type: 'text', text: '{"workflows":[]}' }] },
      }),
    ]
    const derive = createToolCallDeriver()
    expect(derive(messages)).toEqual(groupToolCalls(messages))
  })

  it('keeps untouched ToolCallView references across derives (Object.is)', () => {
    const messages = [
      toolCall('t1', { title: 'Read', status: 'in_progress' }, 'm-t1'),
      toolCall('t2', { title: 'Bash', status: 'completed' }, 'm-t2'),
    ]
    const derive = createToolCallDeriver()
    const first = derive(messages)
    // 一次 SSE chunk 只 upsert t1 对应的消息：t2 的 view 必须引用不变，
    // t1 的 view 必须重建并携带新状态。
    const next = upsertMessage(messages, {
      id: 'm-t1',
      content: { status: 'completed' },
    })!
    const second = derive(next)
    expect(second[1]).toBe(first[1])
    expect(second[1].toolCallId).toBe('t2')
    expect(second[0]).not.toBe(first[0])
    expect(second[0].toolCallId).toBe('t1')
    expect(second[0].status).toBe('completed')
    // 标题合并语义：upsert 的浅合并不带 title，沿用首帧标题。
    expect(second[0].title).toBe('Read')
  })

  it('returns the identical array when only non-tool messages changed', () => {
    const text = message('text', 'agent', { text: '半句' }, 'm-text')
    const messages = [
      toolCall('t1', { title: 'Read', status: 'completed' }),
      text,
    ]
    const derive = createToolCallDeriver()
    const first = derive(messages)
    const next = upsertMessage(messages, {
      id: 'm-text',
      content: { text: '半句补全' },
    })!
    expect(derive(next)).toBe(first)
  })

  it('tracks tool calls appended after the first derive', () => {
    const derive = createToolCallDeriver()
    const existing = toolCall('t1', { title: 'Read', status: 'completed' })
    const first = derive([existing])
    // 真实流程里未触动的消息沿用同一对象（upsertMessage/mergeMessages
    // 引用不变），追加新工具卡时旧卡 view 引用必须保持。
    const second = derive([
      existing,
      toolCall('t2', { title: 'Bash', status: 'pending' }),
    ])
    expect(second.map((view) => view.toolCallId)).toEqual(['t1', 't2'])
    expect(second[0]).toBe(first[0])
  })

  it('recovers when a session switch replaces the whole message list', () => {
    const derive = createToolCallDeriver()
    derive([toolCall('t1', { title: 'Read', status: 'completed' })])
    const replaced = derive([
      toolCall('t9', { title: 'Write', status: 'pending' }, 'other-session'),
    ])
    expect(replaced.map((view) => view.toolCallId)).toEqual(['t9'])
  })

  it('preserves first-seen order when upsert re-sorts messages by seq', () => {
    const early = toolCall('t1', { title: 'Read', status: 'pending' })
    early.seq = 10
    const late = toolCall('t2', { title: 'Bash', status: 'pending' })
    late.seq = 5
    const derive = createToolCallDeriver()
    const views = derive([late, early])
    expect(views.map((view) => view.toolCallId)).toEqual(['t2', 't1'])
  })
})
