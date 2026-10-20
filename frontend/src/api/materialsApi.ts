import { api } from './core'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'
import type {
  MaterialBundleCreateRequest,
  MaterialBundleResponse,
  MaterialPresignRequest,
  MaterialPresignResponse,
  MaterialResponse,
  RunCreateRequest,
  RunCreateResponse,
} from '../types'

/** #1157 phase0：超过该条目数的 run 提交按大批量受理——服务端同步建行
 * 耗时随规模增长（数千条可达分钟级），客户端豁免写超时；对话框侧的
 * 大批量提示共用本阈值（AddItemsDialog）。 */
export const BULK_RUN_SUBMIT_THRESHOLD = 1000

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
    {
      method: 'POST',
      body: JSON.stringify(request),
      // #1157 phase0：大批量提交豁免 120s 写超时——客户端中止并不撤销
      // 服务端动作，过早超时只会把「仍在建行」误报成失败（确定性 run id
      // + dedup 使迟到响应与安全重试都有兜底）。
      timeoutMs:
        request.items.length > BULK_RUN_SUBMIT_THRESHOLD
          ? BULK_REQUEST_TIMEOUT
          : undefined,
    }
  )
}
