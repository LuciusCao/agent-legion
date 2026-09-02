import {
  parseWorkflowYaml,
  type WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'

// approval rework 候选计算（#392 Phase 2）：镜像后端
// approval_rework.execute_rework 的资格校验——target 必须在本审批门的
// 祖先闭包内（排除 start，start 不参与重置）。非祖先目标保存后到运行期
// rework 才被拒，选择器只给运行期必然合法的选项。边的判定源 =
// after ∪ 草稿 edges（手写 v2 YAML 用 edges 声明依赖，与类型切换前置
// 校验同一语义）。
export function approvalReworkCandidates(
  rawYaml: string,
  approvalKey: string
): string[] {
  const draft = parseWorkflowYaml(rawYaml)
  return reworkCandidatesFromDraft(draft, approvalKey)
}

export function reworkCandidatesFromDraft(
  draft: WorkflowYamlObject,
  approvalKey: string
): string[] {
  const nodes = draft.nodes ?? {}
  // 上游映射（to → from 集合）：edges 数组的 to 指向的节点，其上游是
  // 所有 from。after 声明边天然是「本节点 → 依赖」方向，直接用。
  const upstreamOf = new Map<string, Set<string>>()
  for (const edge of draft.edges ?? []) {
    if (!edge.from || !edge.to) continue
    const set = upstreamOf.get(edge.to) ?? new Set<string>()
    set.add(edge.from)
    upstreamOf.set(edge.to, set)
  }
  // 依赖闭包（BFS）：after 声明边 + edges 数组边双源。
  const closure = new Set<string>()
  const queue = [
    ...(nodes[approvalKey]?.after ?? []),
    ...(upstreamOf.get(approvalKey) ?? []),
  ]
  for (let dep = queue.shift(); dep; dep = queue.shift()) {
    if (closure.has(dep)) continue
    closure.add(dep)
    queue.push(...(nodes[dep]?.after ?? []), ...(upstreamOf.get(dep) ?? []))
  }
  return [...closure]
    .filter(
      (key) => key !== approvalKey && nodes[key] && nodes[key].type !== 'start'
    )
    .sort()
}
