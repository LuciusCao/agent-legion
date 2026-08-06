import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { previewBatchRerunJobs } from '../../api/jobRerunPreviewApi'
import { targetBody, type BatchJobTarget } from '../../api/batchTarget'
import { useJobStore } from '../../stores/jobStore'
import { resolveBatchTarget } from '../../stores/job/actions/selectionModeState'

export type BatchRerunPreviewMode =
  | { kind: 'node'; nodeKey: string }
  | { kind: 'failedNode' }
  | { kind: 'category'; category: string }

function toApiMode(
  mode: BatchRerunPreviewMode
): Parameters<typeof previewBatchRerunJobs>[2] {
  if (mode.kind === 'node') return { nodeKey: mode.nodeKey }
  if (mode.kind === 'failedNode') return { fromFailedNode: true }
  return { failureCategory: mode.category }
}

/**
 * Eligible-count preview for filter-based ('allMatching') rerun selections.
 * The target body is built with the same resolveBatchTarget the confirm path
 * uses, so the count matches what a confirm would actually rerun.
 */
export function useBatchRerunPreview(
  workspaceId: string | undefined,
  open: boolean,
  mode: BatchRerunPreviewMode
) {
  // Subscribe to the selection pieces so the query key tracks them; the
  // target itself is recomputed from the store via the shared resolver.
  const selectionMode = useJobStore((s) => s.selectionMode)
  const selectionFilter = useJobStore((s) => s.selectionFilter)
  const excludedIds = useJobStore((s) => s.excludedIds)
  const selectedIds = useJobStore((s) => s.selectedIds)
  const target: BatchJobTarget | null = useMemo(
    () => resolveBatchTarget(useJobStore.getState()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectionMode, selectionFilter, excludedIds, selectedIds]
  )

  return useQuery({
    queryKey: [
      'batch-rerun-preview',
      workspaceId,
      target ? targetBody(target) : null,
      mode,
    ],
    queryFn: () =>
      previewBatchRerunJobs(workspaceId!, target!, toApiMode(mode)),
    enabled: open && !!workspaceId && target !== null,
    staleTime: 10_000,
  })
}
