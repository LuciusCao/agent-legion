/**
 * 预览面板治理动作 hook（#796 验收返工，从 PreviewPanelSection 抽出保体积
 * 预算）：发布/恢复默认（归档）是头部治理行的人工动作——agent 只写草稿
 * （reject_studio_agent_scope 在后端钉死），失败原因收进 actionError 由
 * 治理行展示（role=alert），不抛全局 toast。
 */
import { useState } from 'react'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'

export interface PreviewGovernance {
  publishing: boolean
  actionError: string | null
  publish: () => void
  archive: () => void
}

export function usePreviewGovernance(
  workspaceId: string | undefined
): PreviewGovernance {
  const [actionError, setActionError] = useState<string | null>(null)
  const publishMutation = usePublishPreviewPanel(workspaceId)
  const archiveMutation = useArchivePreviewPanel(workspaceId)

  async function runAction(action: () => Promise<unknown>) {
    setActionError(null)
    try {
      await action()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '操作失败')
    }
  }

  return {
    publishing: publishMutation.isPending,
    actionError,
    publish: () => void runAction(() => publishMutation.mutateAsync()),
    archive: () => void runAction(() => archiveMutation.mutateAsync()),
  }
}
