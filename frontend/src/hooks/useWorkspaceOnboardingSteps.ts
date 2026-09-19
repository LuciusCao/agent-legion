import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import { buildOnboardingSteps } from '../lib/onboardingReadiness'
import {
  buildWorkerOnboardingSteps,
  withWorkerSteps,
} from '../lib/onboardingWorkerSteps'
import { useWorkerReadiness } from './useWorkerReadiness'
import type { WorkflowDefinitionRecord } from '../types'

/**
 * 新 workspace 空态的分步引导：发布 workflow → 接入 Worker → 打开执行开关
 * → 添加第一个任务。前后两步的文案与就绪判定见 onboardingReadiness；中间
 * 两步（PRD 的两个默认关闭开关）见 onboardingWorkerSteps，数据来自
 * useWorkerReadiness（Worker 上线 / 开始领取 / 恢复调度即自动打勾）。
 */
export function useWorkspaceOnboardingSteps(
  workspaceId: string | undefined,
  workflowDefinition: WorkflowDefinitionRecord | null
) {
  const navigate = useNavigate()
  const setAddItemsDialogOpen = useUiStore((s) => s.setAddItemsDialogOpen)
  const readiness = useWorkerReadiness(workspaceId)

  return useMemo(
    () =>
      withWorkerSteps(
        buildOnboardingSteps({
          workflowDefinition,
          goStudio: () =>
            navigate(`/workspaces/${workspaceId}/workflow-studio`),
          openAddItems: () => setAddItemsDialogOpen(true),
        }),
        buildWorkerOnboardingSteps({
          ...readiness,
          workers: readiness.workers ?? [],
          goWorkerSettings: () =>
            navigate(`/workspaces/${workspaceId}/settings`),
          openConsole: (url) => window.open(url, '_blank', 'noopener'),
        })
      ),
    [
      navigate,
      workflowDefinition,
      workspaceId,
      setAddItemsDialogOpen,
      readiness,
    ]
  )
}
