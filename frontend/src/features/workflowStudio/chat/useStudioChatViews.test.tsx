import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { upsertMessage, type ChatMessage } from './studioChatMessages'
import { useStudioChatViews } from './useStudioChatViews'

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

const yaml = 'key: demo\nnodes: []\n'

function draftMessages() {
  return [
    toolCall('t-validate', {
      title: 'mcp__studio__validate_workflow',
      status: 'completed',
      rawInput: { workspace_id: 'ws1', definition_yaml: yaml },
      rawOutput: {
        content: [
          {
            type: 'text',
            text: '{"valid": true, "errors": [], "definition_hash": "h1"}',
          },
        ],
      },
    }),
    toolCall('t-save', {
      title: 'mcp__studio__save_node_code_draft',
      status: 'completed',
      rawInput: { node_key: 'writer', code: 'x' },
      rawOutput: {
        content: [{ type: 'text', text: '{"code_hash": "ch-1"}' }],
      },
    }),
    message('text', 'agent', { text: '草稿已就绪' }, 'm-text'),
  ]
}

describe('useStudioChatViews（#1120 引用稳定化）', () => {
  it('parses draft cards through the incremental path with full outputText', () => {
    const { result } = renderHook(() => useStudioChatViews(draftMessages()))
    expect(result.current.workflowDraft).toMatchObject({
      yaml,
      validated: true,
      draftHash: 'h1',
    })
    expect(result.current.nodeDrafts).toEqual([
      expect.objectContaining({ nodeKey: 'writer', draftHash: 'ch-1' }),
    ])
  })

  it('keeps toolCalls/draft references stable across unrelated text chunks', () => {
    const initial = draftMessages()
    const { result, rerender } = renderHook(
      ({ msgs }) => useStudioChatViews(msgs),
      { initialProps: { msgs: initial } }
    )
    const first = result.current
    // 流式 text chunk 只 upsert 文本消息：工具卡与草稿视图引用必须不变，
    // MessageItem 的 memo 才能命中（workflowDraft/nodeDrafts 是全行 prop）。
    const next = upsertMessage(initial, {
      id: 'm-text',
      content: { text: '草稿已就绪，可以发布' },
    })!
    rerender({ msgs: next })
    expect(result.current.toolCalls).toBe(first.toolCalls)
    expect(result.current.workflowDraft).toBe(first.workflowDraft)
    expect(result.current.nodeDrafts).toBe(first.nodeDrafts)
  })

  it('refreshes the touched card and still reuses identical draft content', () => {
    const initial = draftMessages()
    const { result, rerender } = renderHook(
      ({ msgs }) => useStudioChatViews(msgs),
      { initialProps: { msgs: initial } }
    )
    const first = result.current
    const touchedId = initial[0].id
    // 同一 tool call 的输出更新：只有被触动卡的 view 重建；草稿内容没变，
    // 视图引用也必须稳定（内容等价即复用，不打穿 MessageItem memo）。
    const next = upsertMessage(initial, {
      id: touchedId,
      content: {
        rawOutput: {
          content: [
            {
              type: 'text',
              text: '{"valid": true, "errors": [], "definition_hash": "h1"}',
            },
          ],
        },
      },
    })!
    rerender({ msgs: next })
    expect(result.current.toolCalls).not.toBe(first.toolCalls)
    expect(result.current.toolCalls[0]).not.toBe(first.toolCalls[0])
    expect(result.current.toolCalls[1]).toBe(first.toolCalls[1])
    expect(result.current.workflowDraft).toBe(first.workflowDraft)
    expect(result.current.nodeDrafts).toBe(first.nodeDrafts)
  })
})
