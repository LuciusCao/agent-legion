/**
 * 预览面板 REST（issue #328）：已发布 bundle 的成员级读取 + Studio 治理面
 * （草稿状态 / 发布 / 归档）。发布与归档永远是人工动作——后端在
 * reject_studio_agent_scope 上钉死（STUDIO-AGENT-001）。
 *
 * 类型说明：PreviewPanelVersion 等接口手写镜像后端 pydantic 契约
 * （server/app/routes/studio_agent_preview_contracts.py）——#328 合并时
 * frontend/src/generated/api.ts 尚未再生（生成物由集成方统一更新），
 * api.ts 再生后这里应切换为 components['schemas'] 派生。
 */
import { api } from '../../api/core'

export interface PreviewPanelVersion {
  id: string
  workspace_id: string | null
  entity_key: string
  version: number
  status: 'draft' | 'published' | 'archived'
  html: string
  html_hash: string
  created_by: string
  change_note: string | null
  created_at: string
  published_at: string | null
}

export interface PreviewPanelState {
  published: PreviewPanelVersion | null
  draft: PreviewPanelVersion | null
}

function panelUrl(workspaceId: string, suffix: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/preview-panel${suffix}`
}

/** 成员级读取：job detail 左栏 iframe host 只需要已发布 bundle。 */
export async function fetchPublishedPreviewPanel(
  workspaceId: string
): Promise<PreviewPanelVersion | null> {
  const payload = await api<{ published: PreviewPanelVersion | null }>(
    panelUrl(workspaceId, '/published')
  )
  // 防御：异常代理/旧后端可能回空对象——按「未发布」回落，不让 undefined
  // 进 react-query 缓存（undefined data 会让 query 抛错）。
  return payload.published ?? null
}

/** Studio 治理面读取：发布 + 草稿全态（admin / scoped token）。 */
export async function fetchPreviewPanelState(
  workspaceId: string
): Promise<PreviewPanelState> {
  return api<PreviewPanelState>(panelUrl(workspaceId, ''))
}

export async function publishPreviewPanel(
  workspaceId: string
): Promise<PreviewPanelVersion> {
  return api<PreviewPanelVersion>(panelUrl(workspaceId, '/publish'), {
    method: 'POST',
  })
}

/** 恢复默认：归档全部版本，左栏回落内置/通用预览。 */
export async function archivePreviewPanel(
  workspaceId: string
): Promise<PreviewPanelState> {
  return api<PreviewPanelState>(panelUrl(workspaceId, '/archive'), {
    method: 'POST',
  })
}
