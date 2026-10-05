import type { JobDetail } from '../../types/jobTypes'

/**
 * Job 详情轮询档位（#965）：详情页没有 SSE 通道，只能轮询；按 job 状态
 * 收敛请求量，而不是所有非终态一律 5s。
 * - 活跃执行态（queued / running）：5s，节点进度与产物要及时刷新；
 * - awaiting_approval：人在环，job 本身不会自己前进——降到 30s，只为听到
 *   「别的会话已经审批」这类旁路变化；本页的审批 / 重跑 / 继续等操作在
 *   成功后都会立即 refetch（见 useJobDetail.refreshDetail），新状态落地后
 *   本函数重新求值，档位随之恢复 5s；
 *   例外：并行分支仍有 ready / running 节点时保持 5s（后端在别的分支被
 *   认领后会把 job 翻回 running，见 _lease_control 的状态汇总）；
 * - 终态与 paused（completed / failed / paused / …）：停轮询，重跑等操作
 *   后同样由 refetch 拉回活跃态并恢复 5s。
 */
export const ACTIVE_POLL_INTERVAL_MS = 5_000
export const AWAITING_APPROVAL_POLL_INTERVAL_MS = 30_000

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running'])
const ACTIVE_NODE_STATUSES = new Set(['ready', 'running'])

export function jobDetailPollInterval(
  detail: Pick<JobDetail, 'job' | 'nodes'> | null | undefined
): number | false {
  const status = detail?.job.status ?? ''
  if (ACTIVE_JOB_STATUSES.has(status)) return ACTIVE_POLL_INTERVAL_MS
  if (status !== 'awaiting_approval') return false
  const branchActive = (detail?.nodes ?? []).some((node) =>
    ACTIVE_NODE_STATUSES.has(node.status)
  )
  return branchActive
    ? ACTIVE_POLL_INTERVAL_MS
    : AWAITING_APPROVAL_POLL_INTERVAL_MS
}
