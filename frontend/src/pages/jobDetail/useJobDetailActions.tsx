import { useEffect } from 'react'
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
}

/**
 * Push the actions panel into uiStore only when its visible inputs change:
 * the 5s running-state poll produces a fresh `detail` object every cycle,
 * and pushing a new JSX element into uiStore each time re-renders the whole
 * layout (WorkspaceLayout subscribes to detailPageActions).
 */
export function useJobDetailActions(options: Options) {
  const { detail, nodeCatalog, actionLoading } = options
  const setDetailPageActions = useUiStore((state) => state.setDetailPageActions)
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
    if (!detail) {
      setDetailPageActions(null)
      return
    }
    setDetailPageActions(
      <JobDetailActions
        jobs={[detail.job]}
        workflowDefinition={nodeCatalog}
        loading={actionLoading}
        onRerun={options.onRerun}
        onRunTo={options.onRunTo}
        onContinue={options.onContinue}
        onUpgradeWorkflow={options.onUpgradeWorkflow}
        onPackage={options.onPackage}
        onClearPacked={options.onClearPacked}
        onDelete={options.onDelete}
        onOpenArtifacts={options.onOpenArtifacts}
      />
    )
    return () => setDetailPageActions(null)
    // deps: `actionLoading` reaches this effect only through actionsSignature
    // above — the 5s running-state poll produces a fresh `detail` object every
    // cycle, and listing the raw value would refire this effect (and re-push
    // a visually identical JSX node into uiStore) on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    actionsSignature,
    nodeCatalog,
    setDetailPageActions,
    detail,
    options.onRerun,
    options.onRunTo,
    options.onContinue,
    options.onUpgradeWorkflow,
    options.onPackage,
    options.onClearPacked,
    options.onDelete,
    options.onOpenArtifacts,
  ])
}
