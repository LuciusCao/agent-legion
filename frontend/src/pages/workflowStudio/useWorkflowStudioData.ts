import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchActiveWorkflowRevision, fetchWorkflowRevisions } from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { useFetchWorkflowRevisionDetail } from './useFetchWorkflowRevisionDetail'
import { useStudioNodeSettings } from './useStudioNodeSettings'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'

export function useWorkflowStudioData(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.workflowStudioData(workspaceId ?? ''),
    queryFn: async () => {
      const [active, history] = await Promise.all([
        fetchActiveWorkflowRevision(workspaceId ?? ''),
        fetchWorkflowRevisions(workspaceId ?? ''),
      ])
      return { active, history }
    },
    enabled: !!workspaceId,
  })

  const loadState: LoadState = !workspaceId
    ? 'empty'
    : query.isPending
      ? 'loading'
      : query.isError
        ? 'error'
        : 'ready'
  const workflow = query.data?.active.workflow ?? null
  const revision = query.data?.active.revision ?? null
  const revisions = query.data?.history.revisions ?? []
  const originalYaml = query.data?.active.definition_yaml ?? ''

  // 保持原 reload 契约：无 workspaceId 时 no-op，错误不抛出（loadState 派生）。
  const { refetch } = query
  const reload = useCallback(async () => {
    if (!workspaceId) return
    await refetch()
  }, [workspaceId, refetch])

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
