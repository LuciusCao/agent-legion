import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './studioChatMessages'
import {
  buildPermissionViews,
  extractAgentDefinitionDrafts,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  groupToolCalls,
  maxSeq,
  parseFirstJson,
  permissionResolutionText,
  planEntries,
  streamingTextId,
  upsertMessage,
} from './studioChatMessages'

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

describe('upsertMessage', () => {
  it('inserts a full message in seq order', () => {
    const a = message('text', 'user', { text: 'a' })
    const b = message('text', 'agent', { text: 'b' })
    const list = upsertMessage([b], a)
    expect(list?.map((m) => m.id)).toEqual([a.id, b.id])
  })

  it('merges a streaming partial update by id, keeping seq/created_at', () => {
    const full = message('text', 'agent', { text: 'hel' })
    const next = upsertMessage([full], {
      id: full.id,
      session_id: 's1',
      kind: 'text',
      role: 'agent',
      content: { text: 'hello' },
    })
    expect(next).not.toBeNull()
    expect(next![0].content.text).toBe('hello')
    expect(next![0].seq).toBe(full.seq)
    expect(next![0].created_at).toBe(full.created_at)
  })

  it('returns null for a partial update of an unknown message', () => {
    expect(
      upsertMessage([], { id: 'ghost', content: { text: 'x' } })
    ).toBeNull()
  })
})

describe('groupToolCalls', () => {
  it('merges tool_call and tool_call_update by toolCallId in first-seen order', () => {
    const messages = [
      toolCall('t1', { title: 'list_workflows', status: 'pending' }),
      toolCall('t2', { title: 'validate_workflow', status: 'pending' }),
      message('tool_call', 'agent', {
        sessionUpdate: 'tool_call_update',
        toolCallId: 't1',
        status: 'completed',
        rawOutput: { content: [{ type: 'text', text: '{"workflows":[]}' }] },
      }),
    ]
    const calls = groupToolCalls(messages)
    expect(calls.map((call) => call.toolCallId)).toEqual(['t1', 't2'])
    expect(calls[0].title).toBe('list_workflows')
    expect(calls[0].status).toBe('completed')
    expect(calls[0].outputText).toBe('{"workflows":[]}')
    expect(calls[1].status).toBe('pending')
  })
})

describe('extractWorkflowDraft', () => {
  const yaml = 'key: demo_video_workflow\nnodes: []\n'
  const validateInput = { workspace_id: 'ws1', definition_yaml: yaml }

  it('returns null when no validate/compare call carries definition_yaml', () => {
    const calls = groupToolCalls([
      toolCall('t1', { title: 'list_workflows', rawInput: {} }),
    ])
    expect(extractWorkflowDraft(calls)).toBeNull()
  })

  it('picks the latest draft yaml with validation and compare meta', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'validate_workflow',
        status: 'completed',
        rawInput: validateInput,
        rawOutput: {
          content: [{ type: 'text', text: '{"valid": true, "errors": []}' }],
        },
      }),
      toolCall('t2', {
        title: 'compare_workflow',
        status: 'completed',
        rawInput: validateInput,
        rawOutput: {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                valid: true,
                summary: {
                  node_changes: [{ type: 'added', node_key: 'n1' }],
                  edge_changes: [{ type: 'added' }, { type: 'added' }],
                },
              }),
            },
          ],
        },
      }),
    ])
    const draft = extractWorkflowDraft(calls)
    expect(draft).not.toBeNull()
    expect(draft!.yaml).toBe(yaml)
    expect(draft!.validated).toBe(true)
    expect(draft!.compareMeta).toBe('新增 1 个节点 · 新增 2 条边')
  })

  it('marks unvalidated when the validate output says invalid', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'validate_workflow',
        status: 'completed',
        rawInput: validateInput,
        rawOutput: {
          content: [
            { type: 'text', text: '{"valid": false, "errors": ["x"]}' },
          ],
        },
      }),
    ])
    expect(extractWorkflowDraft(calls)!.validated).toBe(false)
  })
})

describe('agent / node draft extraction', () => {
  it('extracts agent definition drafts', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_agent_definition_draft',
        rawInput: {
          agent_id: 'assess_agent',
          capability: 'assess',
          runtime: 'velites',
          skill: 'assess_comprehension_difficulty',
        },
      }),
    ])
    const drafts = extractAgentDefinitionDrafts(calls)
    expect(drafts).toHaveLength(1)
    expect(drafts[0].agentId).toBe('assess_agent')
    expect(drafts[0].runtime).toBe('velites')
  })

  it('extracts node code drafts', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_node_code_draft',
        rawInput: {
          workflow_key: 'w',
          node_key: 'assess_difficulty',
          code: 'x',
        },
      }),
    ])
    expect(extractNodeCodeDrafts(calls)).toEqual([
      { toolCallId: 't1', nodeKey: 'assess_difficulty' },
    ])
  })
})

describe('buildPermissionViews', () => {
  const pending = message('permission', 'agent', {
    request_id: 'r1',
    status: 'pending',
    tool_call: { title: 'Bash' },
    options: [
      { optionId: 'o1', name: '允许一次', kind: 'allow_once' },
      { optionId: 'o2', name: '拒绝', kind: 'reject_once' },
    ],
  })

  it('keeps a pending request unresolved until a resolved message arrives', () => {
    const [view] = buildPermissionViews([pending])
    expect(view.resolved).toBe(false)
    expect(view.options.map((option) => option.optionId)).toEqual(['o1', 'o2'])

    const resolved = message('permission', 'user', {
      request_id: 'r1',
      status: 'resolved',
      decision: { deny: true },
    })
    const [done] = buildPermissionViews([pending, resolved])
    expect(done.resolved).toBe(true)
    expect(done.decisionText).toBe('已拒绝')
  })

  it('describes auto-approval channels', () => {
    const auto = message('permission', 'system', {
      status: 'resolved',
      decision: { option_id: 'o1', via: 'auto_approved' },
      tool_call: { title: 'validate_workflow' },
    })
    expect(permissionResolutionText(auto)).toBe(
      '已自动允许（平台工具）：validate_workflow'
    )
    const readOnly = message('permission', 'system', {
      status: 'resolved',
      decision: { option_id: 'o1', via: 'auto_read_only' },
      tool_call: { title: 'Read' },
    })
    expect(permissionResolutionText(readOnly)).toBe(
      '已自动允许（只读工具）：Read'
    )
  })
})

describe('misc readers', () => {
  it('parses the first JSON object from tool output text', () => {
    expect(parseFirstJson('{"valid": true}')?.valid).toBe(true)
    expect(parseFirstJson('not json')).toBeNull()
  })

  it('reads plan entries and drops empty ones', () => {
    const plan = message('plan', 'agent', {
      sessionUpdate: 'plan',
      entries: [
        { content: '读 active 定义', status: 'completed' },
        { content: '', status: 'pending' },
      ],
    })
    expect(planEntries(plan)).toEqual([
      { content: '读 active 定义', status: 'completed' },
    ])
  })

  it('tracks maxSeq for incremental refills', () => {
    expect(maxSeq([message('text', 'user', { text: 'a' })])).toBe(seq)
  })
})

describe('streamingTextId', () => {
  const turnEnd = () =>
    message('status', 'system', { event: 'turn_end', stop_reason: 'end' })

  it('returns the last agent text message with no terminal status after it', () => {
    const first = message('text', 'agent', { text: '第一轮' })
    const second = message('text', 'agent', { text: '第二轮' })
    expect(streamingTextId([first, turnEnd(), second])).toBe(second.id)
  })

  it('returns null once the turn ended after the last text', () => {
    const text = message('text', 'agent', { text: '好了' })
    expect(streamingTextId([text, turnEnd()])).toBeNull()
  })

  it('ignores user text messages and historical loads without status events', () => {
    const agentText = message('text', 'agent', { text: '答' })
    const userText = message('text', 'user', { text: '问' })
    expect(streamingTextId([agentText, turnEnd(), userText])).toBeNull()
    expect(streamingTextId([userText, agentText])).toBe(agentText.id)
  })
})
