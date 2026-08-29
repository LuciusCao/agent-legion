import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchWorkflowRevisions, fetchWorkspaces } from '../../../api'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useFetchWorkflowRevisionDetail } from './useFetchWorkflowRevisionDetail'
import { useStudioNodeSettings } from '../inspector/useStudioNodeSettings'
import {
  fetchActiveRevisionOrNull,
  resolveEmptyTemplateYaml,
} from '../canvas/workflowStudioEmptyState'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'

export function useWorkflowStudioData(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.workflowStudioData(workspaceId ?? ''),
    queryFn: async () => {
      const [active, history, workspaces] = await Promise.all([
        fetchActiveRevisionOrNull(workspaceId ?? ''),
        fetchWorkflowRevisions(workspaceId ?? ''),
        fetchWorkspaces(),
      ])
      return { active, history, workspaces: workspaces.workspaces }
    },
    enabled: !!workspaceId,
  })

  // 空态：workspace 存在但从未发布 revision —— 注入模板 YAML 作为草稿起点；
  // workspace 本身查不到时退回 error。
  const emptyTemplateYaml = resolveEmptyTemplateYaml(query.data, workspaceId)
  const loadState: LoadState = !workspaceId
    ? 'empty'
    : query.isPending
      ? 'loading'
      : query.isError || (query.data?.active === null && !emptyTemplateYaml)
        ? 'error'
        : emptyTemplateYaml
          ? 'empty'
          : 'ready'
  const workflow = query.data?.active?.workflow ?? null
  const revision = query.data?.active?.revision ?? null
  const revisions = query.data?.history.revisions ?? []
  const originalYaml =
    query.data?.active?.definition_yaml ?? emptyTemplateYaml ?? ''

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
