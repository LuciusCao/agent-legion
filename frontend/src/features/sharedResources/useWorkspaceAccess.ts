import { useMemo } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaces } from '../../hooks/useWorkspaces'

export function useWorkspaceAccess(workspaceId: string | undefined) {
  const user = useAuthStore((state) => state.user)
  const isAdmin = user?.role === 'admin'
  const workspacesQuery = useWorkspaces()
  const current = workspacesQuery.data?.find(
    (workspace) => workspace.id === workspaceId
  )
  const role = current?.current_user_role
  const canEdit = Boolean(isAdmin || role === 'editor')
  return {
    isAdmin,
    canEdit,
    isViewer: Boolean(user && !isAdmin && role === 'viewer'),
    isLoading: workspacesQuery.isLoading,
    error: workspacesQuery.error,
  }
}

export function useEditableWorkspaceTargets(sourceWorkspaceId: string) {
  const user = useAuthStore((state) => state.user)
  const isAdmin = user?.role === 'admin'
  const workspacesQuery = useWorkspaces()
  const candidates = useMemo(
    () =>
      (workspacesQuery.data ?? []).filter(
        (workspace) => workspace.id !== sourceWorkspaceId
      ),
    [workspacesQuery.data, sourceWorkspaceId]
  )
  return {
    workspaces: candidates.filter(
      (workspace) => isAdmin || workspace.current_user_role === 'editor'
    ),
    isLoading: workspacesQuery.isLoading,
    error: workspacesQuery.error,
  }
}
