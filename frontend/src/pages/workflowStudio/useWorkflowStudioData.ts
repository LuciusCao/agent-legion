import { useCallback, useEffect, useState } from 'react'
import { fetchActiveWorkflowRevision, fetchWorkflowRevisions } from '../../api'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import { useFetchWorkflowRevisionDetail } from './useFetchWorkflowRevisionDetail'
import { useStudioNodeSettings } from './useStudioNodeSettings'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'

export function useWorkflowStudioData(workspaceId: string | undefined) {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [workflow, setWorkflow] = useState(
    null as WorkflowDefinitionRecord | null
  )
  const [revision, setRevision] = useState<WorkflowRevisionSummary | null>(null)
  const [revisions, setRevisions] = useState<WorkflowRevisionSummary[]>([])
  const [originalYaml, setOriginalYaml] = useState('')
  const reload = useCallback(async () => {
    if (!workspaceId) return
    setLoadState('loading')
    try {
      const [active, history] = await Promise.all([
        fetchActiveWorkflowRevision(workspaceId),
        fetchWorkflowRevisions(workspaceId),
      ])
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
  useStudioNodeSettings(workspaceId)
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
