import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { useWorkspaces } from '../../hooks/useWorkspaces'
import type { WorkspaceResponse } from '../../types'

export function useWorkspaceDisplayName(workspaceId: string | undefined) {
  const { data: workspaces } = useWorkspaces()
  const workspaceName = workspaces?.find(
    (workspace) => workspace.id === workspaceId
  )?.name

  // 列表里没有该 workspace 时的单条回退加载；与 AddItemsDialog 共享同一 key。
  const { data: loadedName } = useQuery({
    queryKey: extraQueryKeys.workspace(workspaceId ?? ''),
    queryFn: () =>
      api<WorkspaceResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId ?? '')}`
      ),
    select: (result) => result.workspace.name || null,
    enabled: !!workspaceId && !workspaceName,
  })

  return workspaceName || loadedName || workspaceId || 'workspace'
}
