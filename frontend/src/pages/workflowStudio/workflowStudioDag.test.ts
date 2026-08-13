import { describe, expect, it } from 'vitest'
import { buildDagNodes } from './workflowStudioDag'

const workflow = {
  key: 'wf',
  label: 'Workflow',
  intake: { modes: [] },
  nodes: [
    {
      key: 'start',
      label: 'Start',
      capability: 'fetch',
      after: [],
      inputs: [],
      outputs: ['input.json'],
    },
    {
      key: 'branch',
      label: 'Branch',
      capability: 'classify',
      after: [],
      inputs: ['input.json'],
      outputs: ['decision.json'],
    },
    {
      key: 'done',
      label: 'Done',
      capability: 'finish',
      after: [],
      inputs: ['decision.json'],
      outputs: [],
      terminal: { outcome: 'complete' },
    },
  ],
  edges: [
    { source: 'start', target: 'branch' },
    { source: 'branch', target: 'done' },
    { source: 'branch', target: 'start' },
  ],
}

describe('buildDagNodes', () => {
  it('puts outline and executor information directly on graph nodes', () => {
    const nodes = buildDagNodes(workflow, [
      {
        id: 'code-default',
        kind: 'code',
        capabilities: ['fetch', 'classify', 'finish'],
        global_capacity: 4,
      },
    ])

    expect(nodes[0]).toMatchObject({
      key: 'start',
      capability: 'fetch',
      executorKind: 'code',
    })
    expect(nodes[1].topologyBadges).toContain('branch')
    expect(nodes[2]).toMatchObject({
      terminalOutcome: 'complete',
      topologyBadges: ['terminal'],
    })
  })

  it('resolves agent, executor binding and unbound state per node', () => {
    const nodes = buildDagNodes(
      workflow,
      [
        {
          id: 'code-default',
          kind: 'code',
          capabilities: ['fetch'],
          global_capacity: 4,
        },
      ],
      {
        bindings: [
          {
            workflow_key: 'wf',
            node_key: 'start',
            executor_id: 'code-default',
          },
        ],
        agents: [
          {
            id: 'classifier-v1',
            runtime: 'pi',
            capability: 'classify',
            skill: 'ns/classify',
            tools: [],
            requires_labels: {},
            provider: 'deepseek',
            model: 'm',
            thinking: 'low',
            skill_ref: null,
            skill_commit: null,
          },
        ],
      }
    )

    expect(nodes[0]).toMatchObject({
      key: 'start',
      executorId: 'code-default',
      agentId: null,
      executorUnbound: false,
    })
    expect(nodes[1]).toMatchObject({
      key: 'branch',
      agentId: 'classifier-v1',
      executorId: null,
      executorUnbound: false,
    })
    expect(nodes[2]).toMatchObject({
      key: 'done',
      agentId: null,
      executorId: null,
      executorUnbound: true,
    })
  })
})
