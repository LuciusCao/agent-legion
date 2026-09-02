import { parseWorkflowYaml } from './workflowStudioYamlDraft.parse'

// approval rework 候选计算（#392 Phase 2）：镜像后端
// approval_rework.execute_rework 的资格校验——target 必须在本审批门的
// 祖先闭包内（排除 start，start 不参与重置）。判定源 = after ∪ 草稿
// edges：v1 草稿与 loader 物化（after 派生边 ∪ raw edges）精确一致；
// Studio 往返草稿（revision_format 回写）after 与 edges 恒一致，两版
// 都精确。仅手写 v2 且 after 引用 edges 未连的节点时，前端候选会比
// 运行期 ancestor_closure 宽——该目标在执行 rework 时被后端 fail-closed
// 拒绝并报清晰错误（窄边角，可接受）。
export function approvalReworkCandidates(
  rawYaml: string,
  approvalKey: string
): string[] {
  // 渲染路径调用：YAML 编辑中途的非法文本让 parse 抛错时返回空候选
  // （readApprovalNodeConfig 同款防御），编辑合法后自然恢复。
  try {
    const draft = parseWorkflowYaml(rawYaml)
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
    // 依赖闭包（BFS）：after 声明边 + edges 数组边双源。终止条件用
    // !== undefined——after 来自未校验的草稿文本，混入空串 key 时不能
    // 让它静默截断整条遍历。
    const closure = new Set<string>()
    const queue = [
      ...(nodes[approvalKey]?.after ?? []),
      ...(upstreamOf.get(approvalKey) ?? []),
    ]
    for (let dep = queue.shift(); dep !== undefined; dep = queue.shift()) {
      if (!dep || closure.has(dep)) continue
      closure.add(dep)
      queue.push(...(nodes[dep]?.after ?? []), ...(upstreamOf.get(dep) ?? []))
    }
    return [...closure]
      .filter(
        (key) =>
          key !== approvalKey && nodes[key] && nodes[key].type !== 'start'
      )
      .sort()
  } catch {
    return []
  }
}
