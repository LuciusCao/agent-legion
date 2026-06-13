export interface DagNode {
  key: string
  label?: string | null
  after?: string[] | null
}

export function ancestorClosure(nodes: DagNode[], targetKey: string): string[] {
  const nodeMap = new Map(nodes.map((n) => [n.key, n]))
  const closure = new Set<string>()
  const stack = [targetKey]

  while (stack.length > 0) {
    const key = stack.pop()!
    if (closure.has(key)) continue
    const node = nodeMap.get(key)
    if (!node) continue
    closure.add(key)
    for (const parent of node.after ?? []) {
      if (!closure.has(parent)) {
        stack.push(parent)
      }
    }
  }

  return Array.from(closure)
}

export function isAncestor(
  nodes: DagNode[],
  targetKey: string,
  candidateKey: string
): boolean {
  if (targetKey === candidateKey) return false
  const closure = ancestorClosure(nodes, targetKey)
  return closure.includes(candidateKey)
}

export function validateRunTo(
  nodes: DagNode[],
  targetKey: string,
  startKey?: string
): { valid: boolean; message?: string } {
  const nodeMap = new Map(nodes.map((n) => [n.key, n]))
  const target = nodeMap.get(targetKey)
  if (!target) {
    return { valid: false, message: `目标节点 ${targetKey} 不存在` }
  }

  if (startKey === undefined) {
    return { valid: true }
  }

  const start = nodeMap.get(startKey)
  if (!start) {
    return { valid: false, message: `起始节点 ${startKey} 不存在` }
  }

  if (startKey === targetKey) {
    return { valid: false, message: '起始节点不能等于目标节点' }
  }

  const closure = ancestorClosure(nodes, targetKey)
  if (!closure.includes(startKey)) {
    return {
      valid: false,
      message: `起始节点 ${startKey} 不在目标节点 ${targetKey} 的依赖范围内`,
    }
  }

  return { valid: true }
}
