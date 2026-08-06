import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createWorkspace, updateWorkspace, deleteWorkspace } from '../api'
import { queryKeys } from '../lib/queryKeys'

type UpdateWorkspaceFields = {
  name?: string
  description?: string
  default_workflow_key?: string
  default_entity?: string
  resource_config?: Record<string, unknown>
  intake_config?: Record<string, unknown>
}

/**
 * Workspace 增删改 mutation。成功后失效 workspaces 列表查询，
 * currentWorkspace 等派生态随缓存自动更新，无需手动 patch 列表。
 */
export function useCreateWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      name,
      workflowKey,
    }: {
      name: string
      workflowKey: string
    }) => createWorkspace(name, workflowKey),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces() }),
  })
}

export function useUpdateWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      fields,
    }: {
      id: string
      fields: UpdateWorkspaceFields
    }) => updateWorkspace(id, fields),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces() }),
  })
}

export function useDeleteWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteWorkspace(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces() }),
  })
}
