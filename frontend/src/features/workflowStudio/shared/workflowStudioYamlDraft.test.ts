import { describe, expect, it } from 'vitest'
import {
  patchWorkflowEdgeCondition,
  patchWorkflowLabel,
  patchWorkflowNodeAcceptedItemTypes,
  patchWorkflowNodeInputs,
  patchWorkflowNodeLabel,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from './workflowStudioYamlDraft'
import { patchWorkflowNodeExecution } from './workflowStudioYamlDraft.execution'

const yaml = `key: demo
label: Demo
schema_version: 2
intake:
  modes: {}
nodes:
  fetch:
    label: Fetch
    capability: fetch_items
    after: []
    inputs: []
    outputs:
      - questions.json
  done:
    label: Done
    capability: assemble
    after:
      - fetch
    inputs:
      - questions.json
    outputs:
      - manifest.json
    terminal:
      outcome: uploadable
`

describe('workflowStudioYamlDraft node patches', () => {
  it('patches a node label', () => {
    expect(patchWorkflowNodeLabel(yaml, 'fetch', 'Fetch Questions')).toContain(
      'label: Fetch Questions'
    )
  })

  it('patches node inputs and outputs', () => {
    const withInputs = patchWorkflowNodeInputs(yaml, 'done', [
      'questions.json',
      'review.json',
    ])
    expect(withInputs).toContain('review.json')

    const withOutputs = patchWorkflowNodeOutputs(withInputs, 'done', [
      'manifest.json',
      'summary.json',
    ])
    expect(withOutputs).toContain('summary.json')
  })

  it('patches terminal outcome and can clear terminal', () => {
    const changed = patchWorkflowNodeTerminalOutcome(yaml, 'done', 'archived')
    expect(changed).toContain('outcome: archived')

    const cleared = patchWorkflowNodeTerminalOutcome(changed, 'done', '')
    expect(cleared).not.toContain('terminal:')
  })

  it('patches workflow label', () => {
    expect(patchWorkflowLabel(yaml, 'Demo v2')).toContain('label: Demo v2')
  })

  it('patches node execution settings and removes empty inheritance values', () => {
    const configured = patchWorkflowNodeExecution(
      yaml,
      'fetch',
      'model',
      'gpt-5'
    )
    expect(configured).toContain('execution:')
    expect(configured).toContain('model: gpt-5')

    const inherited = patchWorkflowNodeExecution(
      configured,
      'fetch',
      'model',
      ''
    )
    expect(inherited).not.toContain('execution:')
  })

  it('preserves start-node type and accepted_item_types through patches', () => {
    // start 节点的入口契约字段不得像 config_schema 那样在 yaml 往返中丢失。
    const yamlWithStart = `key: demo
label: Demo
nodes:
  _start:
    type: start
    accepted_item_types:
      - material
  fetch:
    label: Fetch
    capability: fetch_items
    after:
      - _start
`
    const changed = patchWorkflowNodeLabel(yamlWithStart, 'fetch', 'Fetch v2')
    expect(changed).toContain('type: start')
    expect(changed).toContain('accepted_item_types:')
    expect(changed).toContain('- material')
  })

  it('patches accepted_item_types on an existing start node', () => {
    const yamlWithStart = `key: demo
label: Demo
nodes:
  _start:
    type: start
    accepted_item_types:
      - material
      - ref
`
    const changed = patchWorkflowNodeAcceptedItemTypes(
      yamlWithStart,
      '_start',
      ['material', 'ref', 'bundle']
    )
    expect(changed).toContain('type: start')
    expect(changed).toContain('- bundle')
    const again = patchWorkflowNodeAcceptedItemTypes(changed, '_start', [
      'material',
    ])
    expect(again).not.toContain('- ref')
    expect(again).not.toContain('- bundle')
  })

  it('creates a start entry when the node is missing from the draft YAML', () => {
    // loader 注入的合成 _start 节点不在 draft YAML 文本里：补建而不是抛错。
    const changed = patchWorkflowNodeAcceptedItemTypes(yaml, '_start', [
      'material',
      'ref',
    ])
    expect(changed).toContain('_start:')
    expect(changed).toContain('type: start')
    expect(changed).toContain('accepted_item_types:')
    expect(changed).toContain('- material')
  })
})

const yamlWithEdges = `key: demo
label: Demo
schema_version: 2
intake:
  modes: {}
nodes:
  branch:
    label: Branch
    capability: branch
    after: []
    inputs: []
    outputs: []
edges:
  - source: branch
    target: left
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
  - source: branch
    target: right
`

describe('workflowStudioYamlDraft edge condition patches', () => {
  it('patches an edge condition by index', () => {
    const changed = patchWorkflowEdgeCondition(yamlWithEdges, 0, {
      artifact: 'result.json',
      path: '$.eligible',
      equals: false,
    })
    expect(changed).toContain('equals: false')
  })

  it('clears an edge condition by index', () => {
    const changed = patchWorkflowEdgeCondition(yamlWithEdges, 0, null)
    expect(changed).not.toContain('condition:')
  })

  it('patches the second edge when source and target repeat', () => {
    const yamlWithDuplicateEdges = `key: demo
label: Demo
edges:
  - source: branch
    target: left
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
  - source: branch
    target: left
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
`
    const changed = patchWorkflowEdgeCondition(yamlWithDuplicateEdges, 1, {
      artifact: 'result.json',
      path: '$.eligible',
      equals: false,
    })
    const lines = changed.split('\n')
    const firstEqualsIndex = lines.findIndex((line) =>
      line.includes('equals: true')
    )
    const secondEqualsIndex = lines
      .map((line, idx) => ({ line, idx }))
      .filter(({ line }) => line.includes('equals: false'))
      .pop()?.idx
    expect(firstEqualsIndex).toBeGreaterThan(-1)
    expect(secondEqualsIndex).toBeGreaterThan(firstEqualsIndex)
  })
})
