import { describe, expect, it } from 'vitest'
import { buildDagNodes } from './workflowStudioDag'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'

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
  it('puts outline and execution type information directly on graph nodes', () => {
    // #1180：类型徽标用现行 type 词汇——无 Agent 路由的节点一律是 code
    // 池节点；不再有 executorId 'code' 池键占位（双「code」徽标的来源）。
    const nodes = buildDagNodes(workflow)

    expect(nodes[0]).toMatchObject({
      key: 'start',
      capability: 'fetch',
      executionType: 'code',
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
      executionType: 'code',
      agentId: null,
      executorUnbound: false,
    })
    expect(nodes[1]).toMatchObject({
      key: 'branch',
      agentId: 'classifier-v1',
      executionType: 'agent',
      executorUnbound: false,
    })
    expect(nodes[2]).toMatchObject({
      key: 'done',
      agentId: null,
      executionType: 'code',
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
      executionType: 'code',
    })
  })

  it('carries the declared execution runtime on agent nodes (#1180)', () => {
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
                prompt_mode: '',
                runtime: 'velites',
              },
            }
          : node
      ),
    })

    expect(nodes[1]).toMatchObject({
      executionType: 'agent',
      executionRuntime: 'velites',
    })
  })

  it('gives start and approval nodes no execution type (#1180)', () => {
    // start/approval 不 dispatch（EXEC-WORKFLOW-START-001 /
    // EXEC-APPROVAL-001）：没有执行类型，不渲染类型徽标。
    const nodes = buildDagNodes({
      ...workflow,
      nodes: workflow.nodes.map((node) =>
        node.key === 'start'
          ? { ...node, node_type: 'start' }
          : node.key === 'branch'
            ? { ...node, node_type: 'approval' }
            : node
      ),
    })

    expect(nodes[0].executionType).toBeNull()
    expect(nodes[1].executionType).toBeNull()
    expect(nodes[2].executionType).toBe('code')
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
                prompt_mode: '',
              },
            }
          : node
      ),
    })

    expect(nodes.every((node) => node.executionWarning === undefined)).toBe(
      true
    )
  })

  it('badges approval nodes and keeps them free of execution warnings (#392 Phase 3)', () => {
    const nodes = buildDagNodes({
      ...workflow,
      nodes: workflow.nodes.map((node) =>
        node.key === 'branch' ? { ...node, node_type: 'approval' } : node
      ),
    })

    // approval 专属徽标（EXEC-APPROVAL-001），叠加拓扑徽标（branch）。
    expect(nodes[1].topologyBadges).toEqual(['approval', 'branch'])
    // 审批门不 dispatch：不产生 execution 缺口误报。
    expect(nodes[1].executionWarning).toBeUndefined()
  })

  it('reaches the agent·runtime badge from draft YAML through the draft record (#1180)', () => {
    // r3 评审回归：草稿画布（主编辑视图）走 workflowYamlToDefinitionRecord
    // → buildDagNodes 链——mergeNodeExecution 漏丢 runtime 时草稿画布
    // agent 节点恒只显示「agent」，与只读 revision 视图的「agent·velites」
    // 口径不一致。这里钉住全链路数据到达。
    const draftRecord = workflowYamlToDefinitionRecord(`key: demo
nodes:
  _start:
    type: start
    inputs: []
    outputs: []
  review:
    type: agent
    capability: review
    label: Review
    inputs: []
    outputs: []
    execution:
      provider: openai
      model: gpt-5
      runtime: velites
edges: []
`)
    const nodes = buildDagNodes(draftRecord)
    const review = nodes.find((node) => node.key === 'review')

    expect(review).toMatchObject({
      executionType: 'agent',
      executionRuntime: 'velites',
    })
  })
})
