import { api } from './core'
import type { components } from '../generated/api'

export type WorkspaceSharedMaterialsResponse =
  components['schemas']['WorkspaceSharedMaterialsResponse']
export type SharedMaterialFileEntry =
  components['schemas']['SharedMaterialFileEntry']
export type SharedMaterialFileContent =
  components['schemas']['SharedMaterialFileContent']
export type SharedMaterialMapping =
  components['schemas']['SharedMaterialMapping']
export type SharedMaterialSkillDrift =
  components['schemas']['SharedMaterialSkillDrift']
export type SharedMaterialDriftStatus = SharedMaterialSkillDrift['status']
export type SharedMaterialsPropagateResponse =
  components['schemas']['SharedMaterialsPropagateResponse']
export type SharedMaterialPropagateSkillResult =
  components['schemas']['SharedMaterialPropagateSkillResult']
export type SharedMaterialPropagateStatus =
  SharedMaterialPropagateSkillResult['status']

// Read-only user-session mirror of the studio-agent shared-materials surface
// (issue #643); the listing carries no contents — the viewer fetches single
// files on demand.
export async function getWorkspaceSharedMaterials(
  workspaceId: string
): Promise<WorkspaceSharedMaterialsResponse> {
  return api<WorkspaceSharedMaterialsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/skills-shared`
  )
}

export async function getWorkspaceSharedMaterialFile(
  workspaceId: string,
  path: string
): Promise<SharedMaterialFileContent> {
  return api<SharedMaterialFileContent>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/skills-shared/file?path=${encodeURIComponent(path)}`
  )
}

// Propagation (issue #673): copy the given mapped sources (null = all) into
// each mapped skill repo, commit + new patch tag per skill; per-skill
// results (synced/skipped/failed) come back in the response.
export async function propagateWorkspaceSharedMaterials(
  workspaceId: string,
  sources?: string[]
): Promise<SharedMaterialsPropagateResponse> {
  return api<SharedMaterialsPropagateResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/skills-shared/propagate`,
    { method: 'POST', body: JSON.stringify({ sources: sources ?? null }) }
  )
}
