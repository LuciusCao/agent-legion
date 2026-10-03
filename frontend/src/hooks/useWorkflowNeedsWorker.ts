import type { WorkflowDefinitionRecord } from '../types'
import { workflowWorkerNeeds, type WorkerNeeds } from '../lib/workerDependency'
import { useWorkerConsoleConfig } from './useWorkerConsoleUrl'

/**
 * workflowWorkerNeeds 的 hook 形态：补上部署级的 code_requires_worker
 * （与 Worker 控制台入口同一个缓存查询）。只有含 code 节点时才需要这个
 * 事实，其余情况不发请求。workflowDefinition 为空时 Agent 需求取
 * whenNoWorkflow——任务列表横幅无从判断（false），新 workspace 引导按
 * 「将来要用」保留 Worker 两步（true）。
 */
export function useWorkflowNeedsWorker(
  workflowDefinition: WorkflowDefinitionRecord | null,
  {
    enabled = true,
    whenNoWorkflow,
  }: { enabled?: boolean; whenNoWorkflow: boolean }
): WorkerNeeds {
  const hasCodeNode =
    workflowDefinition?.nodes.some((node) => node.node_type === 'code') ?? false
  const consoleConfig = useWorkerConsoleConfig(enabled && hasCodeNode)
  if (!workflowDefinition) return { agent: whenNoWorkflow, code: false }
  return workflowWorkerNeeds(
    workflowDefinition,
    consoleConfig.data?.code_requires_worker
  )
}
