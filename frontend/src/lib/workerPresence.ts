import type { AgentWorkerSummary } from '../api/agentWorkers'

/**
 * Worker 在线状态的四档口径（schema v83 起 Worker 随状态同步上报领取开关）：
 * - offline：心跳超时
 * - online：在线但未上报开关（旧版 Worker，claim_enabled = null）
 * - claiming：在线且允许领取
 * - not_claiming：在线但未开领取——「一直等待中」的首要嫌疑
 */
export type WorkerPresence = 'offline' | 'online' | 'claiming' | 'not_claiming'

type PresenceSource = Pick<AgentWorkerSummary, 'online' | 'claim_enabled'>

export function workerPresence(worker: PresenceSource): WorkerPresence {
  if (!worker.online) return 'offline'
  if (worker.claim_enabled === true) return 'claiming'
  if (worker.claim_enabled === false) return 'not_claiming'
  return 'online'
}

export const PRESENCE_LABEL: Record<WorkerPresence, string> = {
  offline: '离线',
  online: '在线',
  claiming: '在线·领取中',
  not_claiming: '在线·未领取',
}

export const NOT_CLAIMING_HINT =
  'Worker 已注册但未开始领取任务：到 Worker 控制台点「开始领取」'

/** 状态 chip 的悬停说明：未领取时先讲怎么修，再附最近心跳。 */
export function presenceTitle(
  presence: WorkerPresence,
  heartbeatTitle: string
): string {
  return presence === 'not_claiming'
    ? `${NOT_CLAIMING_HINT}（${heartbeatTitle}）`
    : heartbeatTitle
}

/** 设置页 chip 的样式档：未领取用警示色，在线用激活色，离线不着色。 */
export function presenceChipClass(
  presence: WorkerPresence,
  styles: { chipActive?: string; chipIdle?: string }
): string {
  if (presence === 'not_claiming') return styles.chipIdle ?? ''
  if (presence === 'offline') return ''
  return styles.chipActive ?? ''
}

type FleetSource = Pick<
  AgentWorkerSummary,
  'online' | 'claim_enabled' | 'revoked'
>

export function hasOnlineWorker(workers: FleetSource[]): boolean {
  return workers.some((worker) => worker.online && !worker.revoked)
}

/** 有 Worker 在线且允许领取；旧版 Worker 未上报开关按允许计（不误报）。 */
export function hasClaimingWorker(workers: FleetSource[]): boolean {
  return workers.some(
    (worker) =>
      worker.online && !worker.revoked && worker.claim_enabled !== false
  )
}
