import { describe, expect, it } from 'vitest'
import type { WorkflowDefinitionRecord } from '../../types'
import {
  annotateTopologyWithChanges,
  buildTopologyOrder,
  isBranchNode,
  isEntryNode,
  isTerminalNode,
} from './workflowStudioTopology'

function makeWorkflow(
  nodes: { key: string; label?: string; capability?: string }[],
  edges: { source: string; target: string }[]
): WorkflowDefinitionRecord {
  return {
    key: 'question_comprehension_info',
    label: '题目审题信息生成 DAG',
    intake: { modes: [] },
    nodes: nodes.map((node) => ({
      key: node.key,
      label: node.label ?? node.key,
      capability: node.capability ?? 'cap',
      after: [],
      inputs: [],
      outputs: [],
    })),
    edges,
  }
}

describe('workflowStudioTopology', () => {
  it('orders question_comprehension_info from source to downstream branches to terminal', () => {
    const workflow = makeWorkflow(
      [
        { key: 'fetch_questions' },
        { key: 'classify_comprehension_eligibility' },
        { key: 'generate_key_info' },
        { key: 'review_key_info' },
        { key: 'assemble_comprehension_info' },
      ],
      [
        {
          source: 'fetch_questions',
          target: 'classify_comprehension_eligibility',
        },
        {
          source: 'classify_comprehension_eligibility',
          target: 'generate_key_info',
        },
        {
          source: 'classify_comprehension_eligibility',
          target: 'review_key_info',
        },
        { source: 'generate_key_info', target: 'assemble_comprehension_info' },
        { source: 'review_key_info', target: 'assemble_comprehension_info' },
      ]
    )

    const result = buildTopologyOrder(workflow)

    expect(result.order[0]).toBe('fetch_questions')
    expect(result.order[1]).toBe('classify_comprehension_eligibility')
    expect(result.order[result.order.length - 1]).toBe(
      'assemble_comprehension_info'
    )
    expect(result.cyclic).toBe(false)
    expect(result.disconnected).toEqual([])
  })

  it('puts disconnected nodes after connected nodes', () => {
    const workflow = makeWorkflow(
      [{ key: 'a' }, { key: 'b' }, { key: 'c' }, { key: 'lonely' }],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
      ]
    )

    const result = buildTopologyOrder(workflow)

    expect(result.order).toEqual(['a', 'b', 'c'])
    expect(result.disconnected).toEqual(['lonely'])
  })

  it('detects cycles and reports remaining nodes', () => {
    const workflow = makeWorkflow(
      [{ key: 'a' }, { key: 'b' }, { key: 'c' }],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'a' },
      ]
    )

    const result = buildTopologyOrder(workflow)

    expect(result.cyclic).toBe(true)
    expect(result.order).toEqual([])
    expect(result.disconnected.sort()).toEqual(['a', 'b', 'c'])
  })

  it('returns empty order for null workflow', () => {
    const result = buildTopologyOrder(null)

    expect(result.order).toEqual([])
    expect(result.disconnected).toEqual([])
    expect(result.cyclic).toBe(false)
  })

  it('annotates changed nodes', () => {
    const order = ['a', 'b', 'c']
    const changed = new Set(['b'])

    const items = annotateTopologyWithChanges(order, changed)

    expect(items).toEqual([
      { nodeKey: 'a', badge: null },
      { nodeKey: 'b', badge: 'changed' },
      { nodeKey: 'c', badge: null },
    ])
  })

  it('identifies entry, branch, and terminal nodes', () => {
    const workflow = makeWorkflow(
      [
        { key: 'entry' },
        { key: 'branch' },
        { key: 'leaf_one' },
        { key: 'leaf_two' },
      ],
      [
        { source: 'entry', target: 'branch' },
        { source: 'branch', target: 'leaf_one' },
        { source: 'branch', target: 'leaf_two' },
      ]
    )

    expect(isEntryNode(workflow, 'entry')).toBe(true)
    expect(isEntryNode(workflow, 'branch')).toBe(false)

    expect(isBranchNode(workflow, 'branch')).toBe(true)
    expect(isBranchNode(workflow, 'entry')).toBe(false)

    expect(isTerminalNode(workflow, 'leaf_one')).toBe(true)
    expect(isTerminalNode(workflow, 'leaf_two')).toBe(true)
    expect(isTerminalNode(workflow, 'entry')).toBe(false)
  })
})
