import type { WorkflowDefinitionRecord } from '../../types'

export type TopologyItem = {
  nodeKey: string
  badge: 'entry' | 'branch' | 'terminal' | 'changed' | null
}

export type TopologyOrder = {
  order: string[]
  disconnected: string[]
  cyclic: boolean
}

export function isEntryNode(
  workflow: WorkflowDefinitionRecord | null,
  nodeKey: string
): boolean {
  if (!workflow) return false
  return !workflow.edges.some((edge) => edge.target === nodeKey)
}

export function isTerminalNode(
  workflow: WorkflowDefinitionRecord | null,
  nodeKey: string
): boolean {
  if (!workflow) return false
  return !workflow.edges.some((edge) => edge.source === nodeKey)
}

export function isBranchNode(
  workflow: WorkflowDefinitionRecord | null,
  nodeKey: string
): boolean {
  if (!workflow) return false
  return workflow.edges.filter((edge) => edge.source === nodeKey).length >= 2
}

function buildAdjacency(
  workflow: WorkflowDefinitionRecord
): Map<string, string[]> {
  const adjacency = new Map<string, string[]>()
  for (const node of workflow.nodes) {
    adjacency.set(node.key, [])
  }
  for (const edge of workflow.edges) {
    const neighbors = adjacency.get(edge.source) ?? []
    neighbors.push(edge.target)
    adjacency.set(edge.source, neighbors)
  }
  return adjacency
}

function buildInDegree(
  workflow: WorkflowDefinitionRecord
): Map<string, number> {
  const inDegree = new Map<string, number>()
  for (const node of workflow.nodes) {
    inDegree.set(node.key, 0)
  }
  for (const edge of workflow.edges) {
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1)
  }
  return inDegree
}

function buildConnectedSet(workflow: WorkflowDefinitionRecord): Set<string> {
  const connected = new Set<string>()
  for (const edge of workflow.edges) {
    connected.add(edge.source)
    connected.add(edge.target)
  }
  return connected
}

function topoSortConnected(
  workflow: WorkflowDefinitionRecord,
  connected: Set<string>
): { order: string[]; cyclic: boolean } {
  const adjacency = buildAdjacency(workflow)
  const inDegree = buildInDegree(workflow)
  const queue: string[] = []

  for (const nodeKey of connected) {
    if ((inDegree.get(nodeKey) ?? 0) === 0) {
      queue.push(nodeKey)
    }
  }

  const order: string[] = []
  const processed = new Set<string>()

  while (queue.length > 0) {
    const current = queue.shift()!
    if (processed.has(current)) continue
    order.push(current)
    processed.add(current)

    const neighbors = adjacency.get(current) ?? []
    for (const neighbor of neighbors) {
      const newDegree = (inDegree.get(neighbor) ?? 0) - 1
      inDegree.set(neighbor, newDegree)
      if (
        newDegree === 0 &&
        !processed.has(neighbor) &&
        connected.has(neighbor)
      ) {
        queue.push(neighbor)
      }
    }
  }

  const cyclic = processed.size < connected.size
  return { order, cyclic }
}

export function buildTopologyOrder(
  workflow: WorkflowDefinitionRecord | null
): TopologyOrder {
  if (!workflow || workflow.nodes.length === 0) {
    return { order: [], disconnected: [], cyclic: false }
  }

  const connected = buildConnectedSet(workflow)
  const { order, cyclic } = topoSortConnected(workflow, connected)

  const disconnected: string[] = []
  for (const node of workflow.nodes) {
    if (!connected.has(node.key)) {
      disconnected.push(node.key)
    }
  }

  if (cyclic) {
    const processed = new Set(order)
    for (const nodeKey of connected) {
      if (!processed.has(nodeKey) && !disconnected.includes(nodeKey)) {
        disconnected.push(nodeKey)
      }
    }
  }

  return {
    order,
    disconnected,
    cyclic,
  }
}

export function annotateTopologyWithChanges(
  order: string[],
  changedNodeKeys: Set<string>
): TopologyItem[] {
  return order.map((nodeKey) => ({
    nodeKey,
    badge: changedNodeKeys.has(nodeKey) ? 'changed' : null,
  }))
}
