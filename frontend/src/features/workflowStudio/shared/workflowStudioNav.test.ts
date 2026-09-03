import { describe, expect, it } from 'vitest'
import type { AgentListItem, WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { nodeKeyForAgent } from './workflowStudioNav'

// #387：openAgent 的落点解析。published 目录（agent-catalog）查不到
// draft-only Agent 时回落 agent-definitions 列表（含 draft），让聊天草稿
// 卡片的「查看草稿」也能定位到节点。

const workflow = {
  key: 'demo',
  label: 'Demo',
  nodes: [
    {
      key: 'generate_key_info',
      label: '生成关键信息',
      capability: 'generate_key_info',
      node_type: 'agent',
      after: [],
      inputs: [],
      outputs: [],
      terminal: null,
    },
  ],
  edges: [],
  intake: { modes: [] },
} satisfies WorkflowDefinitionRecord

const agentCatalog: AgentDefinition[] = [
  {
    id: 'question-key-info-v1',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'demo_workflow/generate_key_info',
    tools: [],
    requires_labels: {},
  },
]

const agentDefinitions: AgentListItem[] = [
  {
    agent_id: 'draft-only-agent',
    capability: 'generate_key_info',
    runtime: 'pi',
    skill: '',
    version: 1,
    status: 'draft',
    has_draft: true,
    published_at: null,
  },
]

describe('nodeKeyForAgent', () => {
  it('resolves a published agent via the agent catalog', () => {
    expect(
      nodeKeyForAgent('question-key-info-v1', workflow, agentCatalog)
    ).toBe('generate_key_info')
  })

  it('falls back to the agent-definitions list for a draft-only agent', () => {
    expect(
      nodeKeyForAgent('draft-only-agent', workflow, [], agentDefinitions)
    ).toBe('generate_key_info')
  })

  it('returns null when the agent is unknown in both sources', () => {
    expect(
      nodeKeyForAgent('ghost', workflow, agentCatalog, agentDefinitions)
    ).toBeNull()
  })

  it('returns null when the capability has no node binding (empty workflow)', () => {
    expect(
      nodeKeyForAgent('draft-only-agent', null, [], agentDefinitions)
    ).toBeNull()
  })

  it('prefers the published catalog over the definitions fallback', () => {
    const both: AgentListItem[] = [
      {
        ...agentDefinitions[0],
        agent_id: 'question-key-info-v1',
        capability: 'generate_key_info',
      },
    ]
    expect(
      nodeKeyForAgent('question-key-info-v1', workflow, agentCatalog, both)
    ).toBe('generate_key_info')
  })
})
