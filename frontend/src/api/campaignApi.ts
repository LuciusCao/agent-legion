import { api } from './core'
import { handleUnauthorized, withCsrfHeader } from './requestAuth'
import type {
  CampaignCreateResponse,
  CampaignListResponse,
  CampaignMode,
  CampaignPreviewRequest,
  CampaignPreviewResponse,
  CampaignRerunTarget,
  CampaignStatusChangeResponse,
  CampaignSubmitInlineTarget,
  CampaignDetailResponse,
} from '../types/campaignTypes'

/**
 * 批量任务（campaign）API 封装（#532 PR-D，设计 §4.2）。请求/响应形状一律
 * 来自 generated/api.ts 派生的 campaignTypes（手写 interface 禁止）。
 *
 * multipart 上传是唯一不走 `api()` 的端点：`api()` 固定注入
 * Content-Type: application/json，会覆盖 FormData 的 boundary 头；这里
 * 只补 CSRF 头（mutating 请求纪律，与 requestAuth 的其余用法一致），
 * 错误语义（401 / 非 2xx detail 解析）与 `api()` 保持一致。
 */

function campaignsBase(workspaceId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/campaigns`
}

/** rerun/upgrade 批量任务的 JSON 创建（filter 或显式 job_ids）。 */
export async function createCampaign(
  workspaceId: string,
  mode: Extract<CampaignMode, 'rerun' | 'upgrade'>,
  rerun: CampaignRerunTarget,
  name = ''
): Promise<CampaignCreateResponse> {
  return api<CampaignCreateResponse>(campaignsBase(workspaceId), {
    method: 'POST',
    body: JSON.stringify({ mode, name, rerun }),
  })
}

/** submit 批量任务的 JSON 创建（行内 items 通道）。 */
export async function createSubmitCampaign(
  workspaceId: string,
  submit: CampaignSubmitInlineTarget,
  name = ''
): Promise<CampaignCreateResponse> {
  return api<CampaignCreateResponse>(campaignsBase(workspaceId), {
    method: 'POST',
    body: JSON.stringify({ mode: 'submit', name, submit }),
  })
}

/** submit 批量任务的 multipart 创建（清单文件通道）。 */
export async function createCampaignFromManifest(
  workspaceId: string,
  manifest: File,
  options?: { name?: string; watermark?: number; batch_size?: number }
): Promise<CampaignCreateResponse> {
  const form = new FormData()
  form.append('mode', 'submit')
  if (options?.name) {
    form.append('name', options.name)
  }
  if (options?.watermark != null) {
    form.append('watermark', String(options.watermark))
  }
  if (options?.batch_size != null) {
    form.append('batch_size', String(options.batch_size))
  }
  form.append('manifest', manifest)
  const response = await fetch(`${campaignsBase(workspaceId)}/upload`, {
    method: 'POST',
    body: form,
    headers: withCsrfHeader('POST', {}),
  })
  if (response.status === 401)
    handleUnauthorized(`${campaignsBase(workspaceId)}/upload`)
  if (!response.ok) {
    const text = await response.text()
    let message = `HTTP ${response.status}`
    try {
      const json = JSON.parse(text)
      const detail = json.detail as string | { message?: string } | undefined
      const inline = typeof detail === 'string' ? detail : detail?.message
      message =
        (typeof inline === 'string' && inline) || json.message || message
    } catch {
      message = `${message}: ${text.slice(0, 200)}`
    }
    throw Object.assign(new Error(message), { status: response.status })
  }
  return (await response.json()) as CampaignCreateResponse
}

/** Dry-run 创建判定（rerun: total/eligible；submit: would_create/would_skip）。 */
export async function previewCampaign(
  workspaceId: string,
  request: CampaignPreviewRequest
): Promise<CampaignPreviewResponse> {
  return api<CampaignPreviewResponse>(`${campaignsBase(workspaceId)}/preview`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function fetchCampaigns(
  workspaceId: string,
  limit = 100
): Promise<CampaignListResponse> {
  return api<CampaignListResponse>(
    `${campaignsBase(workspaceId)}?limit=${encodeURIComponent(limit)}`
  )
}

export async function fetchCampaign(
  workspaceId: string,
  campaignId: string
): Promise<CampaignDetailResponse> {
  return api<CampaignDetailResponse>(
    `${campaignsBase(workspaceId)}/${encodeURIComponent(campaignId)}`
  )
}

export async function pauseCampaign(
  workspaceId: string,
  campaignId: string
): Promise<CampaignStatusChangeResponse> {
  return api<CampaignStatusChangeResponse>(
    `${campaignsBase(workspaceId)}/${encodeURIComponent(campaignId)}/pause`,
    { method: 'POST' }
  )
}

export async function resumeCampaign(
  workspaceId: string,
  campaignId: string
): Promise<CampaignStatusChangeResponse> {
  return api<CampaignStatusChangeResponse>(
    `${campaignsBase(workspaceId)}/${encodeURIComponent(campaignId)}/resume`,
    { method: 'POST' }
  )
}

export async function cancelCampaign(
  workspaceId: string,
  campaignId: string
): Promise<CampaignStatusChangeResponse> {
  return api<CampaignStatusChangeResponse>(
    `${campaignsBase(workspaceId)}/${encodeURIComponent(campaignId)}/cancel`,
    { method: 'POST' }
  )
}
