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
        id: 'local-default',
        kind: 'local',
        capabilities: ['fetch', 'classify', 'finish'],
        global_capacity: 4,
      },
    ])

    expect(nodes[0]).toMatchObject({
      key: 'start',
      capability: 'fetch',
      executorKind: 'local',
    })
    expect(nodes[1].topologyBadges).toContain('branch')
    expect(nodes[2]).toMatchObject({
      terminalOutcome: 'complete',
      topologyBadges: ['terminal'],
    })
  })
})
