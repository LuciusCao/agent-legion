import { useEffect } from 'react'
import {
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query'

type ActiveSnapshot = {
  workspaceId: string
  ids: ReadonlySet<string>
  count: number
}

const snapshotsByClient = new WeakMap<
  QueryClient,
  Map<string, ActiveSnapshot>
>()

/** active 中有旧卡片离开或计数下降时，只失效同一来源的历史视图。 */
export function useActiveMigrationInvalidation(
  workspaceId: string | undefined,
  activeIds: readonly string[] | undefined,
  activeCount: number | undefined,
  endedKey: QueryKey,
  archivedKey: QueryKey
) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!workspaceId || !activeIds || activeCount === undefined) return
    const current: ActiveSnapshot = {
      workspaceId,
      ids: new Set(activeIds),
      count: activeCount,
    }
    const sourceKey = JSON.stringify(endedKey.slice(0, -1))
    let snapshots = snapshotsByClient.get(queryClient)
    if (!snapshots) {
      snapshots = new Map()
      snapshotsByClient.set(queryClient, snapshots)
    }
    const prior = snapshots.get(sourceKey)
    snapshots.set(sourceKey, current)
    if (!prior || prior.workspaceId !== workspaceId) return
    const migrated =
      activeCount < prior.count ||
      [...prior.ids].some((id) => !current.ids.has(id))
    if (!migrated) return
    void queryClient.invalidateQueries({ queryKey: endedKey })
    void queryClient.invalidateQueries({ queryKey: archivedKey })
  }, [workspaceId, activeIds, activeCount, endedKey, archivedKey, queryClient])
}
