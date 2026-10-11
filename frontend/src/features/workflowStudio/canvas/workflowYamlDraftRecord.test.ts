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
  it('tolerates malformed after values on v1 instead of crashing (#1258 复验 P2-1)', () => {
    // after: 5 / true / {x: 1}：合法 YAML、编辑中途自动保存可达。形状守卫
    // 只校验 nodes/edges，after 非数组时 for..of 抛 TypeError——渲染期崩溃
    // 违反本函数自书契约（形状残缺一律回退/容忍）。非数组 after 按无依赖
    // 容忍（发布侧 loader 另拒）。
    for (const badAfter of ['5', 'true', '{x: 1}']) {
      const yaml = [
        'key: demo',
        'nodes:',
        '  _start:',
        '    type: start',
        '  fetch:',
        '    capability: fetch',
        `    after: ${badAfter}`,
        '  review:',
        '    capability: review',
        '    after: [fetch]',
        '',
      ].join('\n')
      const record = workflowYamlToDefinitionRecord(yaml)
      expect(record).not.toBeNull()
      expect(record?.edges).toEqual([
        { source: 'fetch', target: 'review', condition: null },
      ])
    }
  })

  it('does not derive edges for prototype-chain or non-string after deps (#1258 复验 P3-1)', () => {
    // `dep in nodes` 走原型链：after: [toString] / [constructor] 对空 nodes
    // 也为真，会为不存在节点派生幽灵端点边；after: [1] 这类非字符串 dep
    // 同理不得漏进 edges（后端 _load_nodes 对未知 dep 是 raise）。
    const protoYaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  fetch:',
      '    capability: fetch',
      '    after: [toString]',
      '  review:',
      '    capability: review',
      '    after: [constructor]',
      '',
    ].join('\n')
    expect(workflowYamlToDefinitionRecord(protoYaml)?.edges).toEqual([])
    // 非字符串 dep：节点键 '1' 存在时 after: [1]（number）也不得派生。
    const numericYaml = [
      'key: demo',
      'nodes:',
      '  1:',
      '    capability: one',
      '  fetch:',
      '    capability: fetch',
      '    after: [1]',
      '',
    ].join('\n')
    expect(workflowYamlToDefinitionRecord(numericYaml)?.edges).toEqual([])
  })

  it('materializes v1 after into edges like loader._load_edges (#1258 codex P2)', () => {
    // v1（schema_version 缺省或显式 1）：依赖只存于节点 after，loader
    // 物化为边（loader._load_edges）。草稿 record 不同构物化时，画布与
    // inspector 依赖关系退化为 0 入/0 出。
    const v1Yaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  fetch:',
      '    capability: fetch',
      '    after: [_start]',
      '  review:',
      '    capability: review',
      '    after: [fetch]',
      '  dangling:',
      '    capability: dangling',
      '    after: [missing_node]',
      '',
    ].join('\n')
    const expected = [
      { source: '_start', target: 'fetch', condition: null },
      { source: 'fetch', target: 'review', condition: null },
    ]
    expect(workflowYamlToDefinitionRecord(v1Yaml)?.edges).toEqual(expected)
    // 显式 schema_version: 1 同语义（loader 缺省即 1）。
    expect(
      workflowYamlToDefinitionRecord(
        v1Yaml.replace('key: demo', 'key: demo\nschema_version: 1')
      )?.edges
    ).toEqual(expected)
    // 指向不存在节点的 after 不派生边（loader 发布侧另拒未知依赖；
    // 展示模型不画幽灵端点）。
    expect(
      workflowYamlToDefinitionRecord(v1Yaml)?.edges.some(
        (edge) => edge.source === 'missing_node'
      )
    ).toBe(false)
  })

  it('unions explicit edges with derived after edges on v1, dedup by (source, target)', () => {
    // loader v1：after 派生边在先；raw edges 追加，(from,to) 已在派生集合
    // 的跳过（其 when 条件随之不生效——严格同构 _load_edges）。
    const v1Mixed = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  fetch:',
      '    capability: fetch',
      '    after: [_start]',
      '  review:',
      '    capability: review',
      '    after: [fetch]',
      'edges:',
      '  - from: fetch',
      '    to: review',
      '    when: {artifact: r.json, path: $.ok, equals: true}',
      '  - from: review',
      '    to: fetch',
      '',
    ].join('\n')
    expect(workflowYamlToDefinitionRecord(v1Mixed)?.edges).toEqual([
      { source: '_start', target: 'fetch', condition: null },
      { source: 'fetch', target: 'review', condition: null },
      { source: 'review', target: 'fetch', condition: null },
    ])
  })

  it('does not materialize after on v2 (after is an echo field)', () => {
    // v2+ 完全以 edges 列表为准；after 只是 echo，不得物化。
    const v2Yaml = [
      'key: demo',
      'schema_version: 2',
      'nodes:',
      '  _start:',
      '    type: start',
      '  fetch:',
      '    capability: fetch',
      '    after: [_start]',
      'edges: []',
      '',
    ].join('\n')
    expect(workflowYamlToDefinitionRecord(v2Yaml)?.edges).toEqual([])
  })

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
      prompt_mode: '',
    })
    expect(nodes.fetch?.execution).toEqual({
      provider: 'pi',
      model: 'gpt-5',
      thinking: '',
      prompt: '',
      prompt_mode: '',
    })
    expect(nodes._start?.execution).toBeUndefined()
  })

  it('normalizes non-string execution values instead of crashing the canvas (codex P1)', () => {
    // `provider: 1`、`model: true`：合法 YAML、非法契约值。草稿持久化后
    // 重开会反复走到这里——不得抛异常，非字符串按未配置归一（后续由
    // 节点警告徽标提示缺失）。
    const record = workflowYamlToDefinitionRecord(`key: demo
nodes:
  review:
    type: agent
    capability: review
    execution:
      provider: 1
      model: true
`)
    expect(record).not.toBeNull()
    expect(record?.nodes[0]?.execution).toEqual({
      provider: '',
      model: '',
      thinking: '',
      prompt: '',
      prompt_mode: '',
    })
  })
})
