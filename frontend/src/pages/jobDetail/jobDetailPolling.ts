import type { JobDetail } from '../../types/jobTypes'

/**
 * Job 详情轮询档位（#965）：详情页没有 SSE 通道，只能轮询；按 job 状态
 * 收敛请求量，而不是所有非终态一律 5s。
 * - 活跃执行态（queued / running）：5s，节点进度与产物要及时刷新；
 * - awaiting_approval：人在环，job 本身不会自己前进——降到 30s，只为听到
 *   「别的会话已经审批」这类旁路变化；本页的审批 / 重跑 / 继续等操作在
 *   成功后都会立即 refetch（见 useJobDetail.refreshDetail），新状态落地后
 *   本函数重新求值，档位随之恢复 5s；
 *   例外：并行分支仍可前进时保持 5s——任一节点 running，或任一
 *   pending / ready / stale 节点的 after 依赖全部 completed /
 *   not_applicable（可被 worker 认领；认领后后端会把 job 翻回 running，
 *   见 _lease_control 的状态汇总）。审批门下游的 pending 节点不算。
 * - 终态与 paused（completed / failed / paused / …）：停轮询，重跑等操作
 *   后同样由 refetch 拉回活跃态并恢复 5s。
 */
export const ACTIVE_POLL_INTERVAL_MS = 5_000
export const AWAITING_APPROVAL_POLL_INTERVAL_MS = 30_000

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running'])
const WAITING_NODE_STATUSES = new Set(['pending', 'ready', 'stale'])
const SETTLED_NODE_STATUSES = new Set(['completed', 'not_applicable'])

function hasAdvancingBranch(nodes: JobDetail['nodes']): boolean {
  const statusByKey = new Map(nodes.map((node) => [node.node_key, node.status]))
  return nodes.some(
    (node) =>
      node.status === 'running' ||
      (WAITING_NODE_STATUSES.has(node.status) &&
        (node.after ?? []).every((key) =>
          SETTLED_NODE_STATUSES.has(statusByKey.get(key) ?? '')
        ))
  )
}

export function jobDetailPollInterval(
  detail: Pick<JobDetail, 'job' | 'nodes'> | null | undefined
): number | false {
  const status = detail?.job.status ?? ''
  if (ACTIVE_JOB_STATUSES.has(status)) return ACTIVE_POLL_INTERVAL_MS
  if (status !== 'awaiting_approval') return false
  return hasAdvancingBranch(detail?.nodes ?? [])
    ? ACTIVE_POLL_INTERVAL_MS
    : AWAITING_APPROVAL_POLL_INTERVAL_MS
}
