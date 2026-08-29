import { useEffect, useRef } from 'react'
import { useUiStore } from '../../stores/uiStore'
import { JobDetailActions } from '../../components/job/JobDetailActions'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import type { JobDetail } from '../../types/jobTypes'

type Options = {
  detail: JobDetail | null
  nodeCatalog: NodeCatalog | null
  actionLoading: boolean
  onRerun: (nodeKey: string | null, fromFailedNode?: boolean) => void
  onRunTo: (targetKey: string, startKey?: string) => void
  onContinue: () => void
  onUpgradeWorkflow: () => void
  onPackage: () => void
  onClearPacked: () => void
  onDelete: () => void
  onOpenArtifacts: () => void
  onOpenApproval: () => void
}

/**
 * Push the actions panel into uiStore only when its visible inputs change.
 *
 * The 5s running-state poll produces a fresh `detail` object every cycle;
 * pushing a new JSX element into uiStore each time re-renders the whole
 * layout (WorkspaceLayout subscribes to detailPageActions). The effect's ONLY
 * trigger is therefore `actionsSignature` — the current values (detail,
 * nodeCatalog, callbacks) are read through a ref snapshot so a fresh `detail`
 * reference with an unchanged signature never refires the effect.
 */
export function useJobDetailActions(options: Options) {
  const { detail, actionLoading } = options
  const setDetailPageActions = useUiStore((state) => state.setDetailPageActions)
  const snapshotRef = useRef(options)
  useEffect(() => {
    snapshotRef.current = options
  })
  const actionsSignature = detail
    ? [
        detail.job.status,
        detail.job.updated_at,
        detail.job.completed_nodes,
        detail.job.total_nodes,
        actionLoading,
      ].join('|')
    : null
  useEffect(() => {
    if (actionsSignature === null) {
      setDetailPageActions(null)
      return
    }
    const snapshot = snapshotRef.current
    if (!snapshot.detail) {
      setDetailPageActions(null)
      return
    }
    setDetailPageActions(
      <JobDetailActions
        jobs={[snapshot.detail.job]}
        workflowDefinition={snapshot.nodeCatalog}
        loading={snapshot.actionLoading}
        onRerun={snapshot.onRerun}
        onRunTo={snapshot.onRunTo}
        onContinue={snapshot.onContinue}
        onUpgradeWorkflow={snapshot.onUpgradeWorkflow}
        onPackage={snapshot.onPackage}
        onClearPacked={snapshot.onClearPacked}
        onDelete={snapshot.onDelete}
        onOpenArtifacts={snapshot.onOpenArtifacts}
        onOpenApproval={snapshot.onOpenApproval}
      />
    )
    return () => setDetailPageActions(null)
  }, [actionsSignature, setDetailPageActions])
}
