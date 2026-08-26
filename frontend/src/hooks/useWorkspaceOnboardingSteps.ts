import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceSettingsQuery } from './useWorkspaceSettingsQuery'
import { buildOnboardingSteps } from '../lib/onboardingReadiness'
import type { WorkflowDefinitionRecord } from '../types'

/**
 * 新 workspace 空态的分步引导：发布 workflow → 配置 Agent 与接入 → 添加
 * 第一个任务（步骤文案与就绪判定见 onboardingReadiness）。settings 快照
 * 仅在引导实际展示（enabled）时加载，正常 workspace 主页不产生额外请求。
 */
export function useWorkspaceOnboardingSteps(
  workspaceId: string | undefined,
  workflowKey: string | undefined,
  workflowDefinition: WorkflowDefinitionRecord | null,
  enabled: boolean
) {
  const navigate = useNavigate()
  const { data: settingsSnapshot } = useWorkspaceSettingsQuery(
    workspaceId,
    enabled
  )
  const setAddItemsDialogOpen = useUiStore((s) => s.setAddItemsDialogOpen)

  return useMemo(
    () =>
      buildOnboardingSteps({
        workflowKey,
        workflowDefinition,
        agentRoutes: settingsSnapshot?.agentRoutes ?? [],
        agentDefaults: settingsSnapshot?.settings.agentDefaults,
        intakeModes: settingsSnapshot?.settings.intakeModes,
        workspaceId,
        goStudio: () => navigate(`/workspaces/${workspaceId}/workflow-studio`),
        goSettings: () => navigate('settings'),
        openAddItems: () => setAddItemsDialogOpen(true),
      }),
    [
      navigate,
      workflowKey,
      workflowDefinition,
      settingsSnapshot,
      workspaceId,
      setAddItemsDialogOpen,
    ]
  )
}
