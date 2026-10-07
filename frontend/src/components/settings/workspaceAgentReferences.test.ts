import { describe, expect, it } from 'vitest'
import type { AgentListItem, WorkflowDefinitionRecord } from '../../types'
import {
  inlinedNodesByAgent,
  isDraftOnly,
  legacyAgentNodeReferences,
  routedCapability,
} from './workspaceAgentReferences'

function item(overrides: Partial<AgentListItem>): AgentListItem {
  return {
    agent_id: 'a',
    capability: 'cap_a',
    runtime: 'pi',
    skill: '',
    version: 1,
    status: 'published',
    has_draft: false,
    published_capability: 'cap_a',
    published_version: 1,
    ...overrides,
  }
}

function node(
  key: string,
  overrides: Partial<WorkflowDefinitionRecord['nodes'][number]> = {}
): WorkflowDefinitionRecord['nodes'][number] {
  return {
    key,
    label: key,
    capability: 'cap_a',
    node_type: 'agent',
    inputs: [],
    outputs: [],
    after: [],
    ...overrides,
  }
}

describe('workspaceAgentReferences (#906)', () => {
  it('routes by the published capability when a draft renames it', () => {
    const agent = item({
      status: 'draft',
      capability: 'cap_b',
      version: 2,
      has_draft: true,
    })
    expect(isDraftOnly(agent)).toBe(false)
    expect(routedCapability(agent)).toBe('cap_a')
  })

  it('falls back to the draft capability for never-published agents', () => {
    const agent = item({
      status: 'draft',
      capability: 'cap_d',
      published_capability: null,
      published_version: null,
    })
    expect(isDraftOnly(agent)).toBe(true)
    expect(routedCapability(agent)).toBe('cap_d')
  })
})

describe('workspaceAgentReferences (#1079)', () => {
  // 只有未自含（无 execution.runtime）的 agent 节点还回读 Agent 定义。
  it('counts only legacy agent nodes as references', () => {
    const refs = legacyAgentNodeReferences([
      node('legacy'),
      node('inlined', {
        execution: {
          runtime: 'pi',
          provider: '',
          model: '',
          thinking: '',
          prompt: '',
          prompt_mode: '',
        },
      }),
      node('code', { node_type: 'code' }),
    ])
    expect(refs.get('cap_a')?.map((n) => n.key)).toEqual(['legacy'])
  })

  it('groups inlined nodes by agent id', () => {
    const byAgent = inlinedNodesByAgent([
      { node_key: 'n1', node_label: 'N1', agent_id: 'a', agent_version: 1 },
      { node_key: 'n2', node_label: 'N2', agent_id: 'a', agent_version: 1 },
      { node_key: 'n3', node_label: 'N3', agent_id: 'b', agent_version: 2 },
    ])
    expect(byAgent.get('a')?.map((e) => e.node_key)).toEqual(['n1', 'n2'])
    expect(byAgent.get('b')?.map((e) => e.node_key)).toEqual(['n3'])
  })
})
