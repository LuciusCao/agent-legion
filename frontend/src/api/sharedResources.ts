import { api } from './core'
import type {
  SharedResourceDeleteVersionResponse,
  SharedResourceGrantListResponse,
  SharedResourceGrantResponse,
  SharedResourceListResponse,
  SharedResourceResponse,
  SharedResourceSelectionResponse,
  SharedResourceShareResponse,
  SharedResourceSourceInput,
  SharedResourceVersionListResponse,
  SharedResourceVersionResponse,
  RunDetailResponse,
} from '../features/sharedResources/sharedResourceTypes'

const workspacePath = (workspaceId: string) =>
  `/api/workspaces/${encodeURIComponent(workspaceId)}`
const resourcePath = (workspaceId: string, resourceId: string) =>
  `${workspacePath(workspaceId)}/shared-resources/${encodeURIComponent(resourceId)}`

export async function listSharedResources(
  workspaceId: string,
  signal?: AbortSignal
): Promise<SharedResourceListResponse> {
  const resources: SharedResourceListResponse['resources'] = []
  let offset = 0
  let total: number
  do {
    const page = await api<SharedResourceListResponse>(
      `${workspacePath(workspaceId)}/shared-resources?limit=200&offset=${offset}`,
      { signal }
    )
    resources.push(...page.resources)
    total = page.total
    offset += page.resources.length
    if (page.resources.length === 0) break
  } while (offset < total)
  return { resources, total, limit: resources.length, offset: 0 }
}

export function getSharedResource(
  workspaceId: string,
  resourceId: string,
  signal?: AbortSignal
): Promise<SharedResourceResponse> {
  return api(resourcePath(workspaceId, resourceId), { signal })
}

export function createSharedResource(
  workspaceId: string,
  input: {
    name: string
    source: SharedResourceSourceInput
    idempotencyKey: string
  }
): Promise<SharedResourceResponse> {
  return api(`${workspacePath(workspaceId)}/shared-resources`, {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      source: input.source,
      idempotency_key: input.idempotencyKey,
    }),
  })
}

export function listSharedResourceVersions(
  workspaceId: string,
  resourceId: string,
  signal?: AbortSignal
): Promise<SharedResourceVersionListResponse> {
  return api(`${resourcePath(workspaceId, resourceId)}/versions`, { signal })
}

export function getSharedResourceVersion(
  workspaceId: string,
  resourceId: string,
  version: number,
  signal?: AbortSignal
): Promise<SharedResourceVersionResponse> {
  return api(
    `${resourcePath(workspaceId, resourceId)}/versions/${encodeURIComponent(version)}`,
    { signal }
  )
}

export function createSharedResourceVersion(
  workspaceId: string,
  resourceId: string,
  input: { source: SharedResourceSourceInput; idempotencyKey: string }
): Promise<SharedResourceVersionResponse> {
  return api(`${resourcePath(workspaceId, resourceId)}/versions`, {
    method: 'POST',
    body: JSON.stringify({
      source: input.source,
      idempotency_key: input.idempotencyKey,
    }),
  })
}

export function selectSharedResourceVersion(
  workspaceId: string,
  resourceId: string,
  input: { version: number; expectedRevision: number }
): Promise<SharedResourceSelectionResponse> {
  return api(`${resourcePath(workspaceId, resourceId)}/selection`, {
    method: 'PUT',
    body: JSON.stringify({
      version: input.version,
      expected_revision: input.expectedRevision,
    }),
  })
}

export function shareSharedResource(
  workspaceId: string,
  resourceId: string,
  targetWorkspaceId: string
): Promise<SharedResourceShareResponse> {
  return api(`${resourcePath(workspaceId, resourceId)}/shares`, {
    method: 'POST',
    body: JSON.stringify({ target_workspace_id: targetWorkspaceId }),
  })
}

export function listSharedResourceGrants(
  workspaceId: string,
  resourceId: string,
  signal?: AbortSignal
): Promise<SharedResourceGrantListResponse> {
  return api(`${resourcePath(workspaceId, resourceId)}/shares`, { signal })
}

export function revokeSharedResource(
  workspaceId: string,
  resourceId: string,
  targetWorkspaceId: string
): Promise<SharedResourceGrantResponse> {
  return api(
    `${resourcePath(workspaceId, resourceId)}/shares/${encodeURIComponent(targetWorkspaceId)}`,
    { method: 'DELETE' }
  )
}

export function deleteSharedResourceVersion(
  workspaceId: string,
  resourceId: string,
  version: number
): Promise<SharedResourceDeleteVersionResponse> {
  return api(
    `${resourcePath(workspaceId, resourceId)}/versions/${encodeURIComponent(version)}`,
    { method: 'DELETE' }
  )
}

export function fetchRunDetail(
  workspaceId: string,
  runId: string,
  signal?: AbortSignal
): Promise<RunDetailResponse> {
  return api(
    `${workspacePath(workspaceId)}/runs/${encodeURIComponent(runId)}`,
    { signal }
  )
}
