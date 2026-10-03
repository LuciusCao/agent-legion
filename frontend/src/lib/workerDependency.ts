import type { WorkflowDefinitionRecord } from '../types'

/**
 * 「这个 workflow 的任务要靠 Worker 才能跑起来吗」的唯一判定（#875）：
 * Agent 节点总是派给 Worker；code 节点默认由 Host 本地执行，只有实例
 * 处于纯远程模式（executor_runtime.code_capacity == 0，后端以
 * /api/agent-workers/console 的 code_requires_worker 下发）时才同样依赖
 * 在线的 Worker。任务列表的排查横幅与新 workspace 引导的 Worker 两步都
 * 只经此处判定，不要在调用方另写节点类型条件。
 *
 * codeRequiresWorker 未知（部署元数据未加载或加载失败）时按 Host 本地
 * 执行处理，保持默认实例的行为。
 */
export function workflowNeedsWorker(
  workflowDefinition: Pick<WorkflowDefinitionRecord, 'nodes'>,
  codeRequiresWorker: boolean | undefined
): boolean {
  return workflowDefinition.nodes.some(
    (node) =>
      node.node_type === 'agent' ||
      (codeRequiresWorker === true && node.node_type === 'code')
  )
}
