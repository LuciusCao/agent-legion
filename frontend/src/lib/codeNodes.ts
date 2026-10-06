/**
 * 哪些 workflow 节点跑在隐含 code 池（节点级并发上限只对它们有意义）。
 *
 * 显式类型决定（#284）：`type: agent` 节点永远不是 code 节点——即便它是
 * 自含执行档案节点（#933，`execution.runtime`），发布时不物化 Agent 路由。
 * 只按「有无 Agent 路由」判定会把自含 agent 节点误判成 code 节点。
 * Agent 路由仍参与判定，兼容缺 `node_type` 的旧 payload。
 */
export function codeNodeKeys(
  nodes: ReadonlyArray<{ key: string; node_type?: string }>,
  agentRoutes: ReadonlyArray<{ node_key: string }>
): Set<string> {
  const agentRouted = new Set(agentRoutes.map((route) => route.node_key))
  return new Set(
    nodes
      .filter(
        (node) => node.node_type !== 'agent' && !agentRouted.has(node.key)
      )
      .map((node) => node.key)
  )
}
