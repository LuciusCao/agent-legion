import { describe, expect, it } from 'vitest'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'

const FULL_YAML = `key: demo
label: Demo
schema_version: 2
intake:
  modes:
    form:
      label: 表单
      input_field: question
nodes:
  _start:
    type: start
    accepted_item_types: [material]
  fetch:
    label: Fetch
    capability: fetch_items
    skill: group/fetcher
    after: [_start]
    inputs: [question]
    outputs: [questions.json]
    execution:
      provider: pi
      model: gpt-5
  review:
    type: agent
    capability: review
    skill:
      key: group/reviewer
      ref: v2
    after: [fetch]
  gate:
    type: approval
    capability: approve
    after: [review]
  legacy:
    type: node
    capability: assemble
  done:
    capability: upload
    terminal:
      outcome: uploadable
edges:
  - from: fetch
    to: review
    when:
      artifact: result.json
      path: $.ok
      equals: true
  - from: review
    to: done
`

describe('workflowYamlToDefinitionRecord', () => {
  it('maps all node, edge and metadata fields', () => {
    const record = workflowYamlToDefinitionRecord(FULL_YAML)
    expect(record).not.toBeNull()
    expect(record?.key).toBe('demo')
    expect(record?.label).toBe('Demo')
    expect(record?.intake).toEqual({
      modes: [{ key: 'form', label: '表单', input_field: 'question' }],
    })

    const nodes = Object.fromEntries(
      (record?.nodes ?? []).map((node) => [node.key, node])
    )
    expect(nodes._start).toMatchObject({
      node_type: 'start',
      accepted_item_types: ['material'],
      label: '_start',
      capability: '',
      after: [],
      inputs: [],
      outputs: [],
    })
    expect(nodes.fetch).toMatchObject({
      node_type: 'code',
      label: 'Fetch',
      capability: 'fetch_items',
      after: ['_start'],
      inputs: ['question'],
      outputs: ['questions.json'],
      // 裸 skill 字符串无 ref：#322 归一化后 ref 恒非空（空即 latest，跟随仓库 HEAD）
      skill: { key: 'group/fetcher', ref: 'latest' },
      execution: { provider: 'pi', model: 'gpt-5', thinking: '', prompt: '' },
    })
    expect(nodes.review).toMatchObject({
      node_type: 'agent',
      skill: { key: 'group/reviewer', ref: 'v2' },
    })
    expect(nodes.gate.node_type).toBe('approval')
    // 遗留 type: node 归一化为 code；缺失 type 同样归一化。
    expect(nodes.legacy.node_type).toBe('code')
    expect(nodes.done).toMatchObject({
      node_type: 'code',
      terminal: { outcome: 'uploadable' },
    })
    expect(nodes.fetch.terminal).toBeUndefined()
    expect(nodes._start.execution).toBeUndefined()

    expect(record?.edges).toEqual([
      {
        source: 'fetch',
        target: 'review',
        condition: { artifact: 'result.json', path: '$.ok', equals: true },
      },
      { source: 'review', target: 'done', condition: null },
    ])
  })

  it('tolerates missing nodes/edges/intake and falls back to key as label', () => {
    const record = workflowYamlToDefinitionRecord('key: bare\n')
    expect(record).toEqual({
      key: 'bare',
      label: 'bare',
      intake: { modes: [] },
      nodes: [],
      edges: [],
    })
  })

  it('drops edges missing from or to', () => {
    const record = workflowYamlToDefinitionRecord(
      'key: demo\nedges:\n  - from: a\n  - to: b\n'
    )
    expect(record?.edges).toEqual([])
  })

  it('returns null for syntactically invalid YAML', () => {
    expect(
      workflowYamlToDefinitionRecord('key: demo\nnodes: [broken')
    ).toBeNull()
  })

  it('returns null for non-mapping YAML', () => {
    expect(workflowYamlToDefinitionRecord('- just\n- a\n- list\n')).toBeNull()
    expect(workflowYamlToDefinitionRecord('')).toBeNull()
  })

  it('returns null instead of throwing for malformed node shapes', () => {
    // `nodes:\n  review:`（值为 null）、字符串节点、数组节点：语法合法但
    // 形状残缺，必须回退而不是在渲染期抛异常。
    expect(
      workflowYamlToDefinitionRecord('key: demo\nnodes:\n  review:\n')
    ).toBeNull()
    expect(
      workflowYamlToDefinitionRecord('key: demo\nnodes:\n  review: oops\n')
    ).toBeNull()
    expect(
      workflowYamlToDefinitionRecord('key: demo\nnodes:\n  - review\n')
    ).toBeNull()
  })

  it('returns null instead of throwing for malformed edge shapes', () => {
    expect(
      workflowYamlToDefinitionRecord('key: demo\nedges:\n  notalist: true\n')
    ).toBeNull()
    expect(
      workflowYamlToDefinitionRecord('key: demo\nedges:\n  -\n')
    ).toBeNull()
    expect(
      workflowYamlToDefinitionRecord('key: demo\nedges:\n  - oops\n')
    ).toBeNull()
  })

  it('merges top-level execution defaults into non-start nodes (#333)', () => {
    // 对齐后端 loader：顶层 execution 默认合并进每个非 start 节点（节点值
    // 优先），草稿记录的节点 execution 即有效值，与 published 快照同形。
    const record = workflowYamlToDefinitionRecord(`key: demo
execution:
  provider: openai
  model: gpt-5
nodes:
  _start:
    type: start
  review:
    type: agent
    capability: review
  fetch:
    capability: fetch
    execution:
      provider: pi
`)
    const nodes = Object.fromEntries(
      (record?.nodes ?? []).map((node) => [node.key, node])
    )
    expect(nodes.review?.execution).toEqual({
      provider: 'openai',
      model: 'gpt-5',
      thinking: '',
      prompt: '',
    })
    expect(nodes.fetch?.execution).toEqual({
      provider: 'pi',
      model: 'gpt-5',
      thinking: '',
      prompt: '',
    })
    expect(nodes._start?.execution).toBeUndefined()
  })
})
