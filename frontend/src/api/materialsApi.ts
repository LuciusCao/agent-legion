import { api } from './core'
import type {
  MaterialBundleCreateRequest,
  MaterialBundleResponse,
  MaterialPresignRequest,
  MaterialPresignResponse,
  MaterialResponse,
  RunCreateRequest,
  RunCreateResponse,
} from '../types'

export async function presignMaterial(
  workspaceId: string,
  request: MaterialPresignRequest
): Promise<MaterialPresignResponse> {
  return api<MaterialPresignResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/materials/presign`,
    { method: 'POST', body: JSON.stringify(request) }
  )
}

export async function completeMaterial(
  workspaceId: string,
  materialId: string
): Promise<MaterialResponse> {
  return api<MaterialResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/materials/${encodeURIComponent(materialId)}/complete`,
    { method: 'POST' }
  )
}

export async function createMaterialBundle(
  workspaceId: string,
  request: MaterialBundleCreateRequest
): Promise<MaterialBundleResponse> {
  return api<MaterialBundleResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/material-bundles`,
    { method: 'POST', body: JSON.stringify(request) }
  )
}

export async function createRun(
  workspaceId: string,
  request: RunCreateRequest
): Promise<RunCreateResponse> {
  return api<RunCreateResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/runs`,
    { method: 'POST', body: JSON.stringify(request) }
  )
}
