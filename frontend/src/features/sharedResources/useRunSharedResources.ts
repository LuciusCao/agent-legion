import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSharedResourceRunConfirmation } from '../../api/sharedResources'
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
  const confirmationQuery = useQuery({
    queryKey: [
      ...sharedResourceKeys.list(workspaceId ?? ''),
      'run-confirmation',
    ],
    queryFn: async ({ signal }) => {
      try {
        return await fetchSharedResourceRunConfirmation(workspaceId!, signal)
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
    () =>
      Array.from(
        new Set(
          confirmationQuery.data?.nodes.flatMap((node) => node.resource_ids) ??
            []
        )
      ),
    [confirmationQuery.data]
  )
  const resources = declaredIds.map((id) => {
    const resource = confirmationQuery.data?.resources.find(
      (item) => item.resource_id === id
    )
    return {
      id,
      name: resource?.name ?? '无法读取的资源',
      version: resource?.version ?? null,
      unavailable:
        !resource ||
        resource.version === null ||
        resource.access_status === 'revoked',
    }
  })
  return {
    resources,
    hasResources: declaredIds.length > 0,
    hasUnavailable: resources.some((resource) => resource.unavailable),
    isLoading: enabled && confirmationQuery.isLoading,
    isFetching: enabled && confirmationQuery.isFetching,
    isError: confirmationQuery.isError,
    refetch: () => {
      void confirmationQuery.refetch()
    },
  }
}
