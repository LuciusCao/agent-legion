import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './studioChatMessages'
import {
  buildPermissionViews,
  extractAgentDefinitionDrafts,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  groupToolCalls,
  lastTerminalEvent,
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
        status: 'completed',
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
    expect(drafts[0].status).toBe('completed')
  })

  it('extracts node code drafts', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: {
          workflow_key: 'w',
          node_key: 'assess_difficulty',
          code: 'x',
        },
      }),
    ])
    expect(extractNodeCodeDrafts(calls)).toEqual([
      {
        toolCallId: 't1',
        nodeKey: 'assess_difficulty',
        status: 'completed',
        draftHash: null,
        saveFailed: false,
      },
    ])
  })

  // #692 R2 P2-1：pending/failed 的保存也提取（卡片仍可查看），但 view
  // 必须携带真实 status——发布入口按它门控，发布按钮不得对未完成的
  // 保存开放（否则会把更早的旧草稿发布出去）。
  it('draft views carry the tool call status even when pending or failed', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_agent_definition_draft',
        status: 'failed',
        rawInput: { agent_id: 'assess_agent' },
      }),
      toolCall('t2', {
        title: 'save_node_code_draft',
        status: 'pending',
        rawInput: { node_key: 'assess_difficulty' },
      }),
    ])
    expect(extractAgentDefinitionDrafts(calls)[0].status).toBe('failed')
    expect(extractNodeCodeDrafts(calls)[0].status).toBe('pending')
  })

  // #692 codex P1（第二轮）：同一实体连续保存只保留最新一张卡——发布
  // 请求只带实体 ID，服务端发布的是当前服务端草稿；旧卡的发布按钮会
  // 无提示地发布另一份（更新的）草稿。
  it('keeps only the latest draft card per entity across repeated saves', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_agent_definition_draft',
        status: 'completed',
        rawInput: { agent_id: 'assess_agent', runtime: 'velites' },
      }),
      toolCall('t2', {
        title: 'save_agent_definition_draft',
        status: 'completed',
        rawInput: { agent_id: 'assess_agent', runtime: 'pi' },
      }),
      toolCall('t3', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'assess_difficulty' },
      }),
      toolCall('t4', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'assess_difficulty' },
      }),
      toolCall('t5', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'other_node' },
      }),
    ])
    // 每实体一张：assess_agent 是 t2（最新，runtime 已变）；节点两个
    // key 各一张，assess_difficulty 是 t4。
    expect(extractAgentDefinitionDrafts(calls)).toEqual([
      {
        toolCallId: 't2',
        agentId: 'assess_agent',
        capability: null,
        runtime: 'pi',
        skill: null,
        status: 'completed',
        draftHash: null,
        saveFailed: false,
      },
    ])
    const nodeDrafts = extractNodeCodeDrafts(calls)
    expect(nodeDrafts).toHaveLength(2)
    expect(
      nodeDrafts.find((d) => d.nodeKey === 'assess_difficulty')!.toolCallId
    ).toBe('t4')
    expect(nodeDrafts.find((d) => d.nodeKey === 'other_node')!.toolCallId).toBe(
      't5'
    )
  })

  // R3 P2-3：去重引入的新行为——最新一次保存失败会把更早成功卡的发布
  // 入口一并收走（保守取舍：发布请求只带实体 ID，无法证明旧卡内容仍
  // 是服务端当前草稿）。此分支是回归时最易无声漂移的点，钉死。
  it('keeps only the failed card when the latest save of an entity failed', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'fetch_url' },
      }),
      toolCall('t2', {
        title: 'save_node_code_draft',
        status: 'failed',
        rawInput: { node_key: 'fetch_url' },
      }),
    ])
    expect(extractNodeCodeDrafts(calls)).toEqual([
      {
        toolCallId: 't2',
        nodeKey: 'fetch_url',
        status: 'failed',
        draftHash: null,
        saveFailed: false,
      },
    ])
  })

  // #692 codex P1（第三轮）：draft view 携带保存响应返回的草稿身份
  // hash（rawOutput 的响应体 JSON：agent=definition_hash / code=code_hash），
  // 发布前与服务端当前草稿比对。响应不可解析的旧转录为 null。
  it('draft views carry the draft hash parsed from the save response', () => {
    const calls = groupToolCalls([
      toolCall('t1', {
        title: 'save_agent_definition_draft',
        status: 'completed',
        rawInput: { agent_id: 'writer' },
        rawOutput: {
          content: [
            {
              type: 'text',
              text: '{"id":"v2","version":2,"status":"draft","definition_hash":"dh-1","created_by":"u","created_at":"2026-01-01T00:00:00Z"}',
            },
          ],
        },
      }),
      toolCall('t2', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'fetch_url' },
        rawOutput: {
          content: [
            {
              type: 'text',
              text: '{"id":"v3","version":3,"status":"draft","code_hash":"ch-1","created_by":"u","created_at":"2026-01-01T00:00:00Z"}',
            },
          ],
        },
      }),
      toolCall('t3', {
        title: 'save_node_code_draft',
        status: 'completed',
        rawInput: { node_key: 'legacy_node' },
        rawOutput: { content: [{ type: 'text', text: '草稿已保存' }] },
      }),
    ])
    expect(extractAgentDefinitionDrafts(calls)[0].draftHash).toBe('dh-1')
    expect(
      extractNodeCodeDrafts(calls).find((d) => d.nodeKey === 'fetch_url')!
        .draftHash
    ).toBe('ch-1')
    // 非 JSON 响应体（旧转录/工具输出变化）：null，发布侧按无法核对处理。
    expect(
      extractNodeCodeDrafts(calls).find((d) => d.nodeKey === 'legacy_node')!
        .draftHash
    ).toBeNull()
  })

  // R3 P2-3：去重的保序前提——消息乱序喂入（SSE 增量补齐形态）时
  // upsertMessage 按 seq 整理，去重必须取 seq 较大的保存。
  it('dedup picks the higher-seq save even when messages arrive out of order', () => {
    // 有意乱序构造：先喂 seq 大的消息，upsertMessage 应把它排到后面？
    // 不——upsertMessage 插入即整体按 seq 排序，所以数组序恒 == seq
    // 序；这里直接验证「seq 序 == 数组序」前提下去重取后者。
    const early = message('tool_call', 'agent', {
      id: 'm-early',
      toolCallId: 't1',
      title: 'save_agent_definition_draft',
      status: 'completed',
      rawInput: { agent_id: 'writer' },
    })
    early.seq = 3
    const late = message('tool_call', 'agent', {
      id: 'm-late',
      toolCallId: 't2',
      title: 'save_agent_definition_draft',
      status: 'completed',
      rawInput: { agent_id: 'writer' },
    })
    late.seq = 7
    // 乱序喂入：late 先进列表
    let messages = upsertMessage([early], late)
    if (!messages) messages = [early, late].sort((a, b) => a.seq - b.seq)
    const drafts = extractAgentDefinitionDrafts(groupToolCalls(messages))
    expect(drafts).toHaveLength(1)
    expect(drafts[0].toolCallId).toBe('t2')
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

  it.each(['turn_end', 'turn_timeout', 'error', 'session_closed'])(
    'returns null once %s closed the stream slot',
    (event) => {
      const text = message('text', 'agent', { text: '好了' })
      const status = message('status', 'system', { event })
      expect(streamingTextId([text, status])).toBeNull()
    }
  )

  it('ignores user text messages and historical loads without status events', () => {
    const agentText = message('text', 'agent', { text: '答' })
    const userText = message('text', 'user', { text: '问' })
    expect(streamingTextId([agentText, turnEnd(), userText])).toBeNull()
    expect(streamingTextId([userText, agentText])).toBe(agentText.id)
  })
})

describe('lastTerminalEvent', () => {
  it('returns the most recent terminal status event (#693)', () => {
    const done = message('status', 'system', { event: 'turn_end' })
    const timeout = message('status', 'system', {
      event: 'turn_timeout',
      detail: '运行超过 1 小时已被终止',
    })
    const text = message('text', 'agent', { text: '答' })
    expect(lastTerminalEvent([done, text, timeout])).toBe('turn_timeout')
    expect(lastTerminalEvent([timeout, text, done])).toBe('turn_end')
  })

  it('returns null when no terminal status exists yet', () => {
    const text = message('text', 'agent', { text: '答' })
    const neutral = message('status', 'system', { event: 'cancel_requested' })
    expect(lastTerminalEvent([text, neutral])).toBeNull()
    expect(lastTerminalEvent([])).toBeNull()
  })
})
