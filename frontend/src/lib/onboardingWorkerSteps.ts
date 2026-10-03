import type { AgentWorkerSummary } from '../api/agentWorkers'
import { hasClaimingWorker, hasOnlineWorker } from './workerPresence'
import { needsAnyWorker, type WorkerNeeds } from './workerDependency'
import { needsConsoleUrl, workersMeetingNeeds } from './workerNeedsFleet'

export interface WorkerOnboardingInput {
  /** 本 workspace 视角的 Worker 列表（按 scoped token 注册过滤）。 */
  workers: AgentWorkerSummary[]
  /** workspace 调度是否暂停（顶栏「已暂停／运行中」）。 */
  paused: boolean | undefined
  /** 需要 Worker 承接的执行类型（lib/workerDependency）；缺省按需要 Agent Worker。 */
  needs?: WorkerNeeds
  /** 部署级 Worker 控制台地址（空串 = 未配置）。 */
  consoleUrl: string
  goWorkerSettings: () => void
  resumeScheduling: () => void
  /** 打开控制台的方式（组件传 window.open；纯函数不碰全局）。 */
  openConsole: (url: string) => void
}

export interface OnboardingStep {
  icon: string
  title: string
  description: string
  unlocked: boolean
  completed?: boolean
  actionLabel: string
  onAction: () => void
}

/**
 * PRD「接入 Worker」「打开执行开关」两步（#333 后引导只剩发布与添加任务，
 * 而「提交了但一直没动」的两个默认关闭开关正是新用户最常卡住的地方）。
 * 完成判定：接入 = 本 workspace 有 Worker 在线；执行开关 = 有 Worker 允许
 * 领取（旧版 Worker 未上报按允许计）且调度未暂停。
 */
export function buildWorkerOnboardingSteps(
  input: WorkerOnboardingInput
): OnboardingStep[] {
  const needs = input.needs ?? { agent: true, code: false }
  const needsWorker = needsAnyWorker(needs)
  // 只有能承接所需执行类型的 Worker 才算就绪（#875：纯远程实例的 code
  // 节点不认 agent-only Worker）。
  const capable = workersMeetingNeeds(input.workers, needs)
  const online = !needsWorker || hasOnlineWorker(capable)
  const ready =
    online &&
    (!needsWorker || hasClaimingWorker(capable)) &&
    input.paused === false
  const consoleUrl = needsConsoleUrl(input.workers, needs, input.consoleUrl)
  const steps = [
    {
      icon: 'smart_toy',
      title: '接入 Worker',
      description: needs.code
        ? '为本 workspace 签发 Key，到 Worker 控制台「配置 → Workspace 访问」添加，并为 Worker 开启 code 并发（本实例的 code 节点只在 Worker 上执行）；可执行 code 的 Worker 上线后这一步自动完成。'
        : '为本 workspace 签发 Key，到 Worker 控制台「配置 → Workspace 访问」添加；Worker 上线后这一步自动完成。',
      unlocked: true,
      completed: online,
      actionLabel: online ? '查看 Worker' : '去接入 Worker',
      onAction: input.goWorkerSettings,
    },
    {
      icon: 'toggle_on',
      title: '打开执行开关',
      description:
        '两个默认关闭的开关：在 Worker 控制台点「开始领取」，并把顶栏的「已暂停」切成「运行中」。',
      unlocked: online && input.paused !== undefined,
      completed: ready,
      actionLabel: input.paused
        ? '恢复调度'
        : consoleUrl
          ? '打开 Worker 控制台'
          : '去 Worker 设置',
      onAction: input.paused
        ? input.resumeScheduling
        : consoleUrl
          ? () => input.openConsole(consoleUrl)
          : input.goWorkerSettings,
    },
  ]
  return needsWorker
    ? steps
    : steps.slice(1).map((step) => ({
        ...step,
        description:
          '把顶栏的「已暂停」切成「运行中」，允许本 workspace 调度任务。',
      }))
}

/** 发布 workflow → 接入 Worker → 打开执行开关 → 添加任务。 */
export function withWorkerSteps(
  core: OnboardingStep[],
  workerSteps: OnboardingStep[]
): OnboardingStep[] {
  const ready = workerSteps.every((step) => step.completed)
  return [
    ...core.slice(0, 1),
    ...workerSteps,
    ...core.slice(1).map((step) => ({
      ...step,
      unlocked: step.unlocked && ready,
    })),
  ]
}
