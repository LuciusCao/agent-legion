import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchActiveWorkflowRevision } from '../../api'
import { listSharedResources } from '../../api/sharedResources'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import { parseWorkflowYaml } from '../workflowStudio/shared/workflowStudioYamlDraft.parse'

export function workflowSharedResourceIds(definitionYaml: string): string[] {
  try {
    const draft = parseWorkflowYaml(definitionYaml)
    const ids = Object.values(draft.nodes ?? {}).flatMap((node) => {
      const nodeType = node.type ?? 'code'
      return (nodeType === 'code' || nodeType === 'node') &&
        Array.isArray(node.shared_resources)
        ? node.shared_resources.filter(
            (id): id is string => typeof id === 'string'
          )
        : []
    })
    return Array.from(new Set(ids))
  } catch {
    return []
  }
}

export function useRunSharedResources(
  workspaceId: string | undefined,
  enabled: boolean
) {
  const workflowQuery = useQuery({
    queryKey: ['shared-resource-active-workflow', workspaceId],
    queryFn: async () => {
      try {
        return await fetchActiveWorkflowRevision(workspaceId!)
      } catch (error) {
        if ((error as { status?: number }).status === 404) return null
        throw error
      }
    },
    enabled: enabled && Boolean(workspaceId),
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const declaredIds = useMemo(
    () => workflowSharedResourceIds(workflowQuery.data?.definition_yaml ?? ''),
    [workflowQuery.data]
  )
  const resourcesQuery = useQuery({
    queryKey: sharedResourceKeys.list(workspaceId ?? ''),
    queryFn: ({ signal }) => listSharedResources(workspaceId!, signal),
    enabled: enabled && Boolean(workspaceId) && declaredIds.length > 0,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const resources = declaredIds.map((id) => {
    const resource = resourcesQuery.data?.resources.find(
      (item) => item.id === id
    )
    return {
      id,
      name: resource?.name ?? '无法读取的资源',
      version: resource?.current_selection?.version ?? null,
      unavailable:
        !resource ||
        !resource.current_selection ||
        resource.access_status === 'revoked',
    }
  })
  return {
    resources,
    hasResources: declaredIds.length > 0,
    hasUnavailable: resources.some((resource) => resource.unavailable),
    isLoading: enabled && (workflowQuery.isLoading || resourcesQuery.isLoading),
    isFetching:
      enabled && (workflowQuery.isFetching || resourcesQuery.isFetching),
    isError: workflowQuery.isError || resourcesQuery.isError,
    refetch: () => {
      void workflowQuery.refetch()
      void resourcesQuery.refetch()
    },
  }
}
