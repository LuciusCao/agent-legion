import { Edge } from '@xyflow/react'

export function buildRelationMaps(rfEdges: Edge[]) {
  const edgeBySource: Record<string, Edge[]> = {}
  const edgeByTarget: Record<string, Edge[]> = {}
  for (const edge of rfEdges) {
    edgeBySource[edge.source] = edgeBySource[edge.source] || []
    edgeBySource[edge.source].push(edge)
    edgeByTarget[edge.target] = edgeByTarget[edge.target] || []
    edgeByTarget[edge.target].push(edge)
  }
  return { edgeBySource, edgeByTarget }
}

export function collectAncestors(
  nodeId: string,
  edgeByTarget: Record<string, Edge[]>,
  ancestors: Set<string>
) {
  for (const edge of edgeByTarget[nodeId] || []) {
    if (!ancestors.has(edge.source)) {
      ancestors.add(edge.source)
      collectAncestors(edge.source, edgeByTarget, ancestors)
    }
  }
}

export function collectDescendants(
  nodeId: string,
  edgeBySource: Record<string, Edge[]>,
  descendants: Set<string>
) {
  for (const edge of edgeBySource[nodeId] || []) {
    if (!descendants.has(edge.target)) {
      descendants.add(edge.target)
      collectDescendants(edge.target, edgeBySource, descendants)
    }
  }
}
