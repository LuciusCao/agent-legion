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
  materialId: string,
  expectedUploadGeneration?: number
): Promise<MaterialResponse> {
  return api<MaterialResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/materials/${encodeURIComponent(materialId)}/complete`,
    // #719：complete 服务端对整个对象重算 sha256，大文件耗时不可预估。
    // #1282 复审 P1（#1269 格子②接线）：携带 presign 时的上传代次——其间
    // 发生过重试（代次推进）时服务端直接 409 且不动行，不再因新代次 key
    // 对象缺失把在途上传误翻 failed。
    {
      method: 'POST',
      timeoutMs: null,
      ...(expectedUploadGeneration === undefined
        ? {}
        : {
            body: JSON.stringify({
              expected_upload_generation: expectedUploadGeneration,
            }),
          }),
    }
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
