import type { WorkflowYamlEdge } from './workflowStudioYamlDraft.parse'

function edgeKey(
  source: string | undefined,
  target: string | undefined
): string {
  return `${source ?? ''}|${target ?? ''}`
}

export function resolveEdgeGlobalIndices(
  edges: { source?: string; target?: string }[],
  draftEdges: WorkflowYamlEdge[]
): number[] {
  const positionsByKey = new Map<string, number[]>()
  draftEdges.forEach((draftEdge, index) => {
    const key = edgeKey(draftEdge.source, draftEdge.target)
    positionsByKey.set(key, [...(positionsByKey.get(key) ?? []), index])
  })

  const consumedByKey = new Map<string, number>()
  return edges.map((edge) => {
    const key = edgeKey(edge.source, edge.target)
    const occurrence = consumedByKey.get(key) ?? 0
    consumedByKey.set(key, occurrence + 1)
    const positions = positionsByKey.get(key)
    if (!positions || occurrence >= positions.length) return -1
    return positions[occurrence]
  })
}
