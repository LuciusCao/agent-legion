import type {
  PackageDeleteResponse,
  PackagesResponse,
  PackageUpdate,
  PackageUpdateResponse,
  WorkspacePackageDeleteResponse,
  WorkspacePackagesResponse,
  WorkspacePackageUpdate,
  WorkspacePackageUpdateResponse,
} from '../types/packageTypes'
import { api } from './core'

export async function fetchPackages(): Promise<PackagesResponse> {
  return api('/api/packages')
}

export async function deletePackage(
  id: number
): Promise<PackageDeleteResponse> {
  return api(`/api/packages/${id}`, { method: 'DELETE' })
}

export async function updatePackage(
  id: number,
  fields: PackageUpdate
): Promise<PackageUpdateResponse> {
  return api(`/api/packages/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  })
}

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
