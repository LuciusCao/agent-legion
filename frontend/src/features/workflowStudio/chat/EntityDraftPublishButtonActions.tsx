import { api } from '../../../api'
import type { components } from '../../../generated/api'

type NodeCodeVersionResponse =
  components['schemas']['WorkflowNodeCodeVersionResponse']

export function entityDraftPublishErrorMessage(err: unknown): string {
  const status = (err as { status?: number } | null)?.status
  // 服务端 CAS 核对的拒绝：草稿在保存后被其他会话/编辑器覆盖。本转录
  // 里不存在带新 hash 的卡（去重只看本会话），刷新重析仍是旧 hash——
  // 指向检查器面板的权威状态（R6 P3-3）。
  if (status === 409)
    return '草稿已被其他会话或编辑器更新，请在检查器面板中从最新草稿发布'
  // 404 no draft：后端对无草稿实体（已发布过/竞态已发布）的拒绝，
  // 原文是英文 "no draft for ..."——卡片语境给用户可行动的中文。
  // （start node 拒绝也走 404，文案不精确但发布仍被拦，后端 detail
  // 已透出；低概率路径，不为它拆分支。）
  if (status === 404) return '没有待发布的草稿（可能刚已发布过）'
  return err instanceof Error ? err.message : String(err)
}

export async function publishEntityDraft({
  workspaceId,
  entityId,
  draftHash,
}: {
  workspaceId: string
  entityId: string
  draftHash: string
}) {
  const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/nodes/${encodeURIComponent(entityId)}/code`
  await api<NodeCodeVersionResponse>(`${base}/publish`, {
    method: 'POST',
    body: JSON.stringify({ expected_hash: draftHash }),
  })
}
