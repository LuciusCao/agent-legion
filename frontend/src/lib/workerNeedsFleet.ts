import type { AgentWorkerSummary } from '../api/agentWorkers'
import type { WorkerNeeds } from './workerDependency'
import { readyWorkerConsoleUrl } from './workerConsoleUrl'
import { hasOnlineWorker } from './workerPresence'

// 按 workflowWorkerNeeds 的结果筛 Worker（#875）：就绪判定与修复入口。

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

/**
 * 修复入口该指向的 Worker 控制台：就绪判定只认 capable，但入口不能跟着
 * 收窄——有能承接的 Worker 在线时去它那里开领取；没有时（例如纯远程实例
 * 只有 agent-only Worker 在线），要去的正是那台需要开启 code 并发的
 * Worker，从完整列表里选。都没有自报地址时回落部署级地址。
 */
export function needsConsoleUrl(
  workers: AgentWorkerSummary[],
  needs: WorkerNeeds,
  fallback: string
): string {
  const capable = workersMeetingNeeds(workers, needs)
  return readyWorkerConsoleUrl(
    hasOnlineWorker(capable) ? capable : workers,
    fallback
  )
}
