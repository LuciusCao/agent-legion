import type { AgentWorkerSummary } from '../api/agentWorkers'
import type { WorkflowDefinitionRecord } from '../types'

/** workflow 的任务需要 Worker 承接哪类执行（#875）。 */
export interface WorkerNeeds {
  agent: boolean
  code: boolean
}

export const NO_WORKER_NEEDS: WorkerNeeds = { agent: false, code: false }

/**
 * 「这个 workflow 的任务要靠什么样的 Worker 才能跑起来」的唯一判定（#875）：
 * Agent 节点总是派给 Worker；code 节点默认由 Host 本地执行，只有实例
 * 处于纯远程模式（executor_runtime.code_capacity == 0，后端以
 * /api/agent-workers/console 的 code_requires_worker 下发）时才同样依赖
 * 能执行 code 的 Worker。任务列表的排查横幅与新 workspace 引导的 Worker
 * 两步都只经此处与 workersMeetingNeeds 判定，不要在调用方另写节点类型或
 * Worker 能力条件。
 *
 * codeRequiresWorker 未知（部署元数据未加载或加载失败）时按 Host 本地
 * 执行处理，保持默认实例的行为。
 */
export function workflowWorkerNeeds(
  workflowDefinition: Pick<WorkflowDefinitionRecord, 'nodes'>,
  codeRequiresWorker: boolean | undefined
): WorkerNeeds {
  const types = new Set(workflowDefinition.nodes.map((n) => n.node_type))
  return {
    agent: types.has('agent'),
    code: codeRequiresWorker === true && types.has('code'),
  }
}

export function needsAnyWorker(needs: WorkerNeeds): boolean {
  return needs.agent || needs.code
}

type CapabilitySource = Pick<AgentWorkerSummary, 'max_code_concurrency'>

/**
 * 能承接 needs 的 Worker 子集，供在线/领取判定使用。每个注册的 Worker
 * 都有 Agent 并发（max_concurrency > 0），所以 Agent 需求不收窄；code
 * 需求只认 max_code_concurrency > 0 的 Worker（注册时已强制其协议版本
 * 支持 code），agent-only Worker 在线不代表 code 节点能被领取。
 */
export function workersMeetingNeeds<T extends CapabilitySource>(
  workers: T[],
  needs: WorkerNeeds
): T[] {
  return needs.code
    ? workers.filter((worker) => worker.max_code_concurrency > 0)
    : workers
}
