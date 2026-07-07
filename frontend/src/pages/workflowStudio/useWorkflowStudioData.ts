import { useCallback, useEffect, useState } from 'react'
import { fetchActiveWorkflowRevision, fetchWorkflowRevisions } from '../../api'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import { useFetchWorkflowRevisionDetail } from './useFetchWorkflowRevisionDetail'
import type { UseWorkflowStudioDataResult } from './useWorkflowStudioData.types'

export function useWorkflowStudioData(
  workspaceId: string | undefined
): UseWorkflowStudioDataResult {
  const [loadState, setLoadState] = useState<
    'loading' | 'ready' | 'empty' | 'error'
  >('loading')
  const [workflow, setWorkflow] = useState<WorkflowDefinitionRecord | null>(
    null
  )
  const [revision, setRevision] = useState<WorkflowRevisionSummary | null>(null)
  const [revisions, setRevisions] = useState<WorkflowRevisionSummary[]>([])
  const [originalYaml, setOriginalYaml] = useState('')
  const reload = useCallback(async () => {
    if (!workspaceId) return
    setLoadState('loading')
    try {
      const active = await fetchActiveWorkflowRevision(workspaceId)
      const history = await fetchWorkflowRevisions(workspaceId)
      setWorkflow(active.workflow)
      setRevision(active.revision)
      setRevisions(history.revisions)
      setOriginalYaml(active.definition_yaml)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [workspaceId])
  useEffect(() => {
    if (!workspaceId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- derive empty state when no workspace is selected
      setLoadState('empty')
      return
    }
    void reload()
  }, [reload, workspaceId])
  const fetchRevisionDetail = useFetchWorkflowRevisionDetail(workspaceId)
  return {
    loadState,
    workflow,
    revision,
    revisions,
    originalYaml,
    reload,
    fetchRevisionDetail,
  }
}
