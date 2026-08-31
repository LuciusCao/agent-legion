import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import { buildOnboardingSteps } from '../lib/onboardingReadiness'
import type { WorkflowDefinitionRecord } from '../types'

/**
 * 新 workspace 空态的分步引导：发布 workflow → 添加第一个任务（步骤文案
 * 与就绪判定见 onboardingReadiness）。#333 起不再读取 settings 快照——
 * 原「配置 Agent 执行」步的 agent 路由判定已随该步移除，agent 节点
 * provider/model 缺口由 Studio 画布实时警报承载。
 */
export function useWorkspaceOnboardingSteps(
  workspaceId: string | undefined,
  workflowDefinition: WorkflowDefinitionRecord | null
) {
  const navigate = useNavigate()
  const setAddItemsDialogOpen = useUiStore((s) => s.setAddItemsDialogOpen)

  return useMemo(
    () =>
      buildOnboardingSteps({
        workflowDefinition,
        goStudio: () => navigate(`/workspaces/${workspaceId}/workflow-studio`),
        openAddItems: () => setAddItemsDialogOpen(true),
      }),
    [navigate, workflowDefinition, workspaceId, setAddItemsDialogOpen]
  )
}
