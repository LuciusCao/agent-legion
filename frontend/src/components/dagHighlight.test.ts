import { describe, expect, it } from 'vitest'
import { MarkerType, type Edge, type Node } from '@xyflow/react'
import type { DagNodeData } from './dag/dagNodeTypes'
import { applyHighlight, hoverReducer, type HoverState } from './dagHighlight'

function makeNode(id: string): Node<DagNodeData> {
  return {
    id,
    type: 'dagNode',
    position: { x: 0, y: 0 },
    data: { label: id, status: 'pending', inputs: [], outputs: [] },
  }
}

function makeEdge(id: string, source: string, target: string): Edge {
  return {
    id,
    source,
    target,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
  }
}

const nodes = ['a', 'b', 'c', 'd', 'e'].map(makeNode)
const edges = [
  makeEdge('ab', 'a', 'b'),
  makeEdge('bc', 'b', 'c'),
  makeEdge('de', 'd', 'e'),
]

function nodeById(result: ReturnType<typeof applyHighlight>, id: string) {
  return result.highlightedNodes.find((node) => node.id === id)!
}

function edgeById(result: ReturnType<typeof applyHighlight>, id: string) {
  return result.highlightedEdges.find((edge) => edge.id === id)!
}

describe('applyHighlight hover transitions', () => {
  it('keeps the untouched normal graph referentially stable', () => {
    const result = applyHighlight(nodes, edges, null)
    expect(result.highlightedNodes).toEqual(nodes)
    expect(result.highlightedEdges).toEqual(edges)
    result.highlightedNodes.forEach((node, index) => {
      expect(node).toBe(nodes[index])
      expect(node.data.active).toBeUndefined()
      expect(node.data.dimmed).toBeUndefined()
    })
    result.highlightedEdges.forEach((edge, index) => {
      expect(edge).toBe(edges[index])
    })
  })

  it('preserves chained-transition references and edge dim rendering', () => {
    const first = applyHighlight(nodes, edges, 'b')
    expect(nodeById(first, 'b').data).toMatchObject({
      active: true,
      dimmed: false,
    })
    expect(nodeById(first, 'd').data.dimmed).toBe(true)
    expect(edgeById(first, 'ab').data?.highlighted).toBe(true)
    expect(edgeById(first, 'de').data?.highlighted).toBe(false)

    const second = applyHighlight(nodes, edges, 'c', first)
    expect(nodeById(second, 'b').data).toMatchObject({
      active: false,
      dimmed: false,
    })
    expect(nodeById(second, 'c').data).toMatchObject({
      active: true,
      dimmed: false,
    })
    expect(nodeById(second, 'a')).toBe(nodeById(first, 'a'))
    // 既有节点语义是返回当前基线对象；边则复用 prev 中视觉态未翻转的对象。
    expect(nodeById(second, 'd')).toBe(nodes[3])
    expect(nodeById(second, 'e')).toBe(nodes[4])
    expect(edgeById(second, 'ab').data?.highlighted).toBe(false)
    expect(edgeById(second, 'bc')).toBe(edgeById(first, 'bc'))
    expect(edgeById(second, 'de')).toBe(edgeById(first, 'de'))
    expect(edgeById(second, 'de').markerEnd).toEqual({
      type: MarkerType.ArrowClosed,
      color: '#d1d5db',
    })

    const cleared = applyHighlight(nodes, edges, null, second)
    cleared.highlightedNodes.forEach((node) => {
      expect(node.data.active).not.toBe(true)
      expect(node.data.dimmed).not.toBe(true)
    })
    cleared.highlightedEdges.forEach((edge) => {
      expect(edge.data?.highlighted).toBeUndefined()
      expect(edge.markerEnd).toEqual({
        type: MarkerType.ArrowClosed,
        color: '#6b7280',
      })
    })
  })
})

describe('hoverReducer', () => {
  it('updates hover identity atomically and bails out on repeated actions', () => {
    const initial: HoverState = { hoveredNode: null, prevActiveNode: null }
    expect(hoverReducer(initial, { type: 'leave' })).toBe(initial)

    const first = hoverReducer(initial, { type: 'enter', id: 'a' })
    expect(first).toEqual({ hoveredNode: 'a', prevActiveNode: null })
    expect(hoverReducer(first, { type: 'enter', id: 'a' })).toBe(first)

    const second = hoverReducer(first, { type: 'enter', id: 'b' })
    expect(second).toEqual({ hoveredNode: 'b', prevActiveNode: 'a' })
    expect(hoverReducer(second, { type: 'leave' })).toEqual({
      hoveredNode: null,
      prevActiveNode: 'b',
    })
  })
})
