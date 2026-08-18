import { describe, expect, it } from 'vitest'
import type { DagGraphEdge, DagGraphNode } from '../../components/dag/DagGraph'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import {
  applyCompareChanges,
  countNodeChanges,
} from './workflowStudioDagChanges'

function makeSummary(
  overrides: Partial<ChangeSummaryViewModel> = {}
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [],
    edgeChanges: [],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(),
    ...overrides,
  }
}

// 画布展示的是 active 基线图：a、b 在基线里，c 是 draft 新增（不在基线）。
const baseNodes: DagGraphNode[] = [
  { key: 'a', label: 'A', status: 'pending', created_at: '' },
  { key: 'b', label: 'B', status: 'pending', created_at: '' },
]
const baseEdges: DagGraphEdge[] = [{ from: 'a', to: 'b' }]

function nodeChange(type: 'added' | 'modified' | 'removed', nodeKey: string) {
  return {
    type,
    nodeKey,
    label: nodeKey.toUpperCase(),
    fields: [],
    severity: 'info' as const,
  }
}

describe('countNodeChanges', () => {
  it('returns null without a summary or without node changes', () => {
    expect(countNodeChanges(null)).toBeNull()
    expect(countNodeChanges(makeSummary())).toBeNull()
  })

  it('counts added/modified/removed node changes', () => {
    const counts = countNodeChanges(
      makeSummary({
        nodeChanges: [
          nodeChange('added', 'c'),
          nodeChange('modified', 'a'),
          nodeChange('removed', 'd'),
        ],
      })
    )
    expect(counts).toEqual({ added: 1, modified: 1, removed: 1, total: 3 })
  })
})

describe('applyCompareChanges', () => {
  it('passes through when there are no node changes', () => {
    const result = applyCompareChanges(baseNodes, baseEdges, null)
    expect(result.nodes).toBe(baseNodes)
    expect(result.edges).toBe(baseEdges)
  })

  it('tags a modified baseline node without ghost styling', () => {
    const { nodes } = applyCompareChanges(
      baseNodes,
      baseEdges,
      makeSummary({ nodeChanges: [nodeChange('modified', 'a')] })
    )
    expect(nodes.find((n) => n.key === 'a')).toMatchObject({
      changeType: 'modified',
      ghost: false,
    })
    expect(nodes.find((n) => n.key === 'b')?.changeType).toBeUndefined()
  })

  it('tags a removed baseline node with ghost styling', () => {
    const { nodes } = applyCompareChanges(
      baseNodes,
      baseEdges,
      makeSummary({ nodeChanges: [nodeChange('removed', 'b')] })
    )
    expect(nodes.find((n) => n.key === 'b')).toMatchObject({
      changeType: 'removed',
      ghost: true,
    })
  })

  it('adds a ghost node and ghost edge for an added draft node', () => {
    const { nodes, edges } = applyCompareChanges(
      baseNodes,
      baseEdges,
      makeSummary({
        nodeChanges: [nodeChange('added', 'c')],
        edgeChanges: [
          {
            type: 'added',
            source: 'b',
            target: 'c',
            beforeCondition: null,
            afterCondition: null,
            severity: 'info',
          },
        ],
      })
    )
    expect(nodes.find((n) => n.key === 'c')).toMatchObject({
      label: 'C',
      changeType: 'added',
      ghost: true,
    })
    expect(edges).toHaveLength(2)
    expect(edges[1]).toEqual({ from: 'b', to: 'c', ghost: true })
  })

  it('skips ghost edges that duplicate an existing edge', () => {
    const { edges } = applyCompareChanges(
      baseNodes,
      baseEdges,
      makeSummary({
        nodeChanges: [nodeChange('modified', 'a')],
        edgeChanges: [
          {
            type: 'added',
            source: 'a',
            target: 'b',
            beforeCondition: null,
            afterCondition: null,
            severity: 'info',
          },
        ],
      })
    )
    expect(edges).toHaveLength(1)
  })
})
