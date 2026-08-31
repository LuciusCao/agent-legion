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
      node_type: 'agent',
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
    // P-0.5：无 Agent 路由的节点一律标记为 code 池节点。
    const nodes = buildDagNodes(workflow)

    expect(nodes[0]).toMatchObject({
      key: 'start',
      capability: 'fetch',
      executorKind: 'code',
      executorId: 'code',
    })
    expect(nodes[1].topologyBadges).toContain('branch')
    expect(nodes[2]).toMatchObject({
      terminalOutcome: 'complete',
      topologyBadges: ['terminal'],
    })
  })

  it('resolves agent routing and code-pool fallback per node', () => {
    const nodes = buildDagNodes(workflow, {
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
    })

    expect(nodes[0]).toMatchObject({
      key: 'start',
      executorId: 'code',
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
      executorId: 'code',
      executorUnbound: false,
    })
  })

  it('keeps a code node on the code pool even when its capability matches an Agent', () => {
    // #284：类型判定只读 node_type；capability 恰好命中 Agent 目录的
    // type=code 节点仍显示为 code 池节点（Agent 闲置）。
    const nodes = buildDagNodes(
      {
        ...workflow,
        nodes: workflow.nodes.map((node) =>
          node.key === 'branch' ? { ...node, node_type: 'code' } : node
        ),
      },
      {
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

    expect(nodes[1]).toMatchObject({
      key: 'branch',
      agentId: null,
      executorId: 'code',
      executorKind: 'code',
    })
  })

  it('injects execution warnings only for agent nodes missing provider/model (#333)', () => {
    // 记录侧节点 execution 是有效值（草稿已经 workflowYamlToDefinitionRecord
    // 合并顶层默认，published 快照经后端 loader 合并）；这里只读节点值。
    const nodes = buildDagNodes(workflow)

    // branch 是 agent 节点且未配置 execution → 警告；start/code 节点不警告。
    expect(nodes[1].executionWarning).toBe(
      '缺 provider / model，该节点跑不起来'
    )
    expect(nodes[0].executionWarning).toBeUndefined()
    expect(nodes[2].executionWarning).toBeUndefined()
  })

  it('injects no warning when the agent node execution is complete', () => {
    const nodes = buildDagNodes({
      ...workflow,
      nodes: workflow.nodes.map((node) =>
        node.key === 'branch'
          ? {
              ...node,
              execution: {
                provider: 'openai',
                model: 'gpt-5',
                thinking: '',
                prompt: '',
              },
            }
          : node
      ),
    })

    expect(nodes.every((node) => node.executionWarning === undefined)).toBe(
      true
    )
  })
})
