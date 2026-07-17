import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { WorkspaceResponse } from '../../types'

export function useWorkspaceDisplayName(workspaceId: string | undefined) {
  const [loaded, setLoaded] = useState<{
    workspaceId: string
    name: string | null
  } | null>(null)
  const workspaceName = useWorkspaceStore((state) => {
    return state.workspaces.find((workspace) => workspace.id === workspaceId)
      ?.name
  })

  useEffect(() => {
    if (!workspaceId || workspaceName) return
    let cancelled = false
    api<WorkspaceResponse>(`/api/workspaces/${encodeURIComponent(workspaceId)}`)
      .then((result) => {
        if (!cancelled) {
          setLoaded({ workspaceId, name: result.workspace.name || null })
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [workspaceId, workspaceName])

  const loadedName =
    loaded && loaded.workspaceId === workspaceId ? loaded.name : null
  return workspaceName || loadedName || workspaceId || 'workspace'
}
