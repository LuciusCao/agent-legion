import type {
  WorkspacePackageDeleteResponse,
  WorkspacePackageUpdate,
  WorkspacePackageUpdateResponse,
  WorkspacePackagesResponse,
} from '../types/packageTypes'
import { api } from './core'

export async function deleteWorkspacePackage(
  workspaceId: string,
  packageId: number
): Promise<WorkspacePackageDeleteResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${packageId}`,
    { method: 'DELETE' }
  )
}

export async function updateWorkspacePackage(
  workspaceId: string,
  packageId: number,
  body: WorkspacePackageUpdate
): Promise<WorkspacePackageUpdateResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${packageId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    }
  )
}

export async function fetchWorkspacePackages(
  workspaceId: string
): Promise<WorkspacePackagesResponse> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/packages`)
}
