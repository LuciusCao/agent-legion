import { api } from '../../api'
import { useAsync } from '../../hooks/useAsync'
import { useWorkspaces } from '../../hooks/useWorkspaces'
import type { WorkspaceResponse } from '../../types'

export function useWorkspaceDisplayName(workspaceId: string | undefined) {
  const { data: workspaces } = useWorkspaces()
  const workspaceName = workspaces?.find(
    (workspace) => workspace.id === workspaceId
  )?.name

  const { data: loaded } = useAsync(async () => {
    if (!workspaceId || workspaceName) return null
    const result = await api<WorkspaceResponse>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}`
    )
    return { workspaceId, name: result.workspace.name || null }
  }, [workspaceId, workspaceName])

  const loadedName =
    loaded && loaded.workspaceId === workspaceId ? loaded.name : null
  return workspaceName || loadedName || workspaceId || 'workspace'
}
