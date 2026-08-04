import { useWorkspaceDisplayName } from './useWorkspaceDisplayName'

export function useWorkflowStudioAppTitle(workspaceId: string | undefined) {
  return `${useWorkspaceDisplayName(workspaceId)} / 编辑工作流`
}
