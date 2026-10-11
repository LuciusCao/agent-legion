import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './studioChatMessages'
import {
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  groupToolCalls,
} from './studioChatMessages'
import {
  extractDraftSavedEvents,
  mergeNodeCodeDrafts,
  mergeWorkflowDraft,
} from './studioChatDraftEvents'

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

function draftEvent(
  content: Record<string, unknown>,
  id?: string
): ChatMessage {
  return message('draft', 'agent', { event: 'draft_saved', ...content }, id)
}

describe('extractDraftSavedEvents', () => {
  it('parses draft_saved messages and ignores everything else', () => {
    const messages = [
      message('text', 'user', { text: 'hi' }),
      // 未知 draft_type / 非 draft_saved 事件 / 非 draft kind 一律忽略。
      draftEvent({ draft_type: 'mystery' }),
      message('status', 'system', {
        event: 'draft_saved',
        draft_type: 'node_code',
      }),
      draftEvent(
        { draft_type: 'node_code', node_key: 'n1', code_hash: 'h1' },
        'ev1'
      ),
      draftEvent(
        {
          draft_type: 'workflow',
          definition_yaml: 'key: a\n',
          definition_hash: 'dh',
        },
        'ev2'
      ),
    ]
    const events = extractDraftSavedEvents(messages)
    expect(events.map((e) => e.messageId)).toEqual(['ev1', 'ev2'])
    expect(events[0]).toMatchObject({
      draftType: 'node_code',
      nodeKey: 'n1',
      codeHash: 'h1',
      definitionYaml: null,
    })
    expect(events[1]).toMatchObject({
      draftType: 'workflow',
      definitionYaml: 'key: a\n',
      definitionHash: 'dh',
    })
  })
})

describe('mergeNodeCodeDrafts', () => {
  it('derives a completed card from a synthetic event alone (subagent save)', () => {
    const messages = [
      draftEvent(
        { draft_type: 'node_code', node_key: 'n1', code_hash: 'h1' },
        'ev1'
      ),
    ]
    const merged = mergeNodeCodeDrafts([], messages)
    expect(merged).toEqual([
      {
        toolCallId: '',
        nodeKey: 'n1',
        status: 'completed',
        draftHash: 'h1',
        saveFailed: false,
        seq: messages[0].seq,
        anchorMessageId: 'ev1',
      },
    ])
  })

  it('keeps the tool-call card when the ACP update is newer (main agent dedup)', () => {
    const messages = [
      toolCall('t1', {
        title: 'save_node_code_draft',
        status: 'in_progress',
        rawInput: { node_key: 'n1' },
      }),
      // 合成事件写在路由返回之前，completed 更新恒在其后。
      draftEvent({ draft_type: 'node_code', node_key: 'n1', code_hash: 'h1' }),
      toolCall('t1', {
        sessionUpdate: 'tool_call_update',
        status: 'completed',
        rawOutput: { content: [{ type: 'text', text: '{"code_hash": "h1"}' }] },
      }),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeNodeCodeDrafts(extractNodeCodeDrafts(calls), messages)
    expect(merged).toHaveLength(1)
    expect(merged[0]).toMatchObject({
      toolCallId: 't1',
      status: 'completed',
      draftHash: 'h1',
      anchorMessageId: null,
    })
  })

  it('lets a later synthetic save supersede an earlier tool-call save', () => {
    const messages = [
      toolCall('t1', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'n1' },
        rawOutput: {
          content: [{ type: 'text', text: '{"code_hash": "old"}' }],
        },
      }),
      draftEvent(
        { draft_type: 'node_code', node_key: 'n1', code_hash: 'new' },
        'ev9'
      ),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeNodeCodeDrafts(extractNodeCodeDrafts(calls), messages)
    expect(merged).toHaveLength(1)
    expect(merged[0]).toMatchObject({
      toolCallId: '',
      draftHash: 'new',
      anchorMessageId: 'ev9',
    })
  })

  it('keeps separate entities on their own anchors', () => {
    const messages = [
      toolCall('t1', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'n1' },
      }),
      draftEvent(
        { draft_type: 'node_code', node_key: 'n2', code_hash: 'h2' },
        'ev2'
      ),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeNodeCodeDrafts(extractNodeCodeDrafts(calls), messages)
    expect(merged.map((d) => [d.nodeKey, d.anchorMessageId])).toEqual([
      ['n1', null],
      ['n2', 'ev2'],
    ])
  })
})

describe('mergeWorkflowDraft', () => {
  const YAML_A = 'key: a\nlabel: A\n'
  const YAML_B = 'key: a\nlabel: B\n'

  function validateCall(id: string, yaml: string) {
    return toolCall(id, {
      title: 'validate_workflow',
      status: 'completed',
      rawInput: { definition_yaml: yaml },
      rawOutput: {
        content: [
          {
            type: 'text',
            text: '{"valid": true, "errors": [], "definition_hash": "dh-a"}',
          },
        ],
      },
    })
  }

  it('derives an unvalidated card from a synthetic save alone', () => {
    const messages = [
      draftEvent(
        {
          draft_type: 'workflow',
          definition_yaml: YAML_A,
          definition_hash: 'dh-a',
        },
        'ev1'
      ),
    ]
    const merged = mergeWorkflowDraft(null, [], messages)
    expect(merged).toEqual({
      yaml: YAML_A,
      validated: false,
      compareMeta: null,
      draftHash: 'dh-a',
      anchorToolCallId: null,
      anchorMessageId: 'ev1',
    })
  })

  it('keeps the validate-derived card when its yaml matches the synthetic save', () => {
    const messages = [
      validateCall('t1', YAML_A),
      // 同一 yaml 的合成保存不覆盖：内容一致，保留 validated 与工具卡锚点。
      draftEvent(
        {
          draft_type: 'workflow',
          definition_yaml: YAML_A,
          definition_hash: 'dh-a',
        },
        'ev1'
      ),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeWorkflowDraft(
      extractWorkflowDraft(calls),
      calls,
      messages
    )
    expect(merged).toMatchObject({
      yaml: YAML_A,
      validated: true,
      draftHash: 'dh-a',
      anchorToolCallId: 't1',
      anchorMessageId: null,
    })
  })

  it('a newer synthetic save of a different yaml wins over stale validate evidence', () => {
    const messages = [
      validateCall('t1', YAML_A),
      draftEvent(
        {
          draft_type: 'workflow',
          definition_yaml: YAML_B,
          definition_hash: 'dh-b',
        },
        'ev1'
      ),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeWorkflowDraft(
      extractWorkflowDraft(calls),
      calls,
      messages
    )
    expect(merged).toMatchObject({
      yaml: YAML_B,
      validated: false,
      draftHash: 'dh-b',
      anchorToolCallId: null,
      anchorMessageId: 'ev1',
    })
  })

  it('a validate completed after the synthetic save wins again', () => {
    const messages = [
      draftEvent(
        {
          draft_type: 'workflow',
          definition_yaml: YAML_B,
          definition_hash: 'dh-b',
        },
        'ev1'
      ),
      validateCall('t1', YAML_A),
    ]
    const calls = groupToolCalls(messages)
    const merged = mergeWorkflowDraft(
      extractWorkflowDraft(calls),
      calls,
      messages
    )
    expect(merged).toMatchObject({
      yaml: YAML_A,
      validated: true,
      anchorToolCallId: 't1',
      anchorMessageId: null,
    })
  })

  it('returns null when there is no evidence at all', () => {
    expect(mergeWorkflowDraft(null, [], [])).toBeNull()
  })
})
