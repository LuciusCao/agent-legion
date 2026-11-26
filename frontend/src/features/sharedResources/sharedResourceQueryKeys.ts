export const sharedResourceKeys = {
  all: (workspaceId: string) => ['shared-resources', workspaceId] as const,
  list: (workspaceId: string) =>
    ['shared-resources', workspaceId, 'list'] as const,
  detail: (workspaceId: string, resourceId: string) =>
    ['shared-resources', workspaceId, resourceId, 'detail'] as const,
  versions: (workspaceId: string, resourceId: string) =>
    ['shared-resources', workspaceId, resourceId, 'versions'] as const,
  version: (workspaceId: string, resourceId: string, version: number) =>
    ['shared-resources', workspaceId, resourceId, 'versions', version] as const,
  grants: (workspaceId: string, resourceId: string) =>
    ['shared-resources', workspaceId, resourceId, 'grants'] as const,
  run: (workspaceId: string, runId: string) =>
    ['shared-resources', workspaceId, 'runs', runId] as const,
}
