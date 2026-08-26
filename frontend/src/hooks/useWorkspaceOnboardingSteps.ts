import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceSettingsQuery } from './useWorkspaceSettingsQuery'

/**
 * 新 workspace 空态的分步引导：发布 workflow → 配置 Agent 与接入 → 添加
 * 第一个任务。步骤 1 的完成看 workflow_key，步骤 2 看 Agent 默认
 * provider/model 是否已配；步骤 3 不设完成态——首个 job 出现后引导
 * 整体消失。后序步骤在前序就绪前保持锁定。
 */
export function useWorkspaceOnboardingSteps(
  workspaceId: string | undefined,
  workflowKey: string | undefined
) {
  const navigate = useNavigate()
  const { data: settingsSnapshot } = useWorkspaceSettingsQuery(workspaceId)
  const agentDefaults = settingsSnapshot?.settings.agentDefaults
  const agentConfigured = !!(agentDefaults?.provider && agentDefaults?.model)
  const setAddItemsDialogOpen = useUiStore((s) => s.setAddItemsDialogOpen)

  return useMemo(
    () => [
      {
        icon: 'account_tree',
        title: '创建并发布 Workflow',
        description: '在 Studio 中编辑 workflow 草稿，对比并发布第一个版本。',
        unlocked: true,
        completed: !!workflowKey,
        actionLabel: '进入 Studio',
        onAction: () => navigate(`/workspaces/${workspaceId}/workflow-studio`),
      },
      {
        icon: 'settings',
        title: '配置 Agent 与接入',
        description: '设置 Agent 默认模型（provider / model）并勾选接入模式。',
        unlocked: !!workflowKey,
        completed: !!workflowKey && agentConfigured,
        actionLabel: '去配置',
        onAction: () => navigate('settings'),
      },
      {
        icon: 'add_task',
        title: '添加第一个任务',
        description: '按接入模式添加条目，启动你的第一个任务。',
        unlocked: !!workflowKey && agentConfigured,
        actionLabel: '添加条目',
        onAction: () => setAddItemsDialogOpen(true),
      },
    ],
    [navigate, workflowKey, agentConfigured, workspaceId, setAddItemsDialogOpen]
  )
}
