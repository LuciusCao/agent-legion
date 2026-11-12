/** #633：workflow 草稿 PUT 的 CAS 失败（409）——服务端草稿已被 agent/其它
 * 会话推进，detail 携带当前草稿；编辑器据此提示用户而非静默覆盖。
 * 从 workflowDraft.ts 拆出（文件体积预算）。
 * #1177 codex P1/P2：transport type 从生成的 OpenAPI 契约派生（后端
 * ``responses={409: WorkflowDraftConflictResponse}`` 立模，detail 封套
 * 内为 WorkflowDraftConflictDetail）——不再手写字段。 */

import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

export type WorkflowDraftConflictDetail =
  ApiSchemas['WorkflowDraftConflictDetail']
export type WorkflowDraftConflictCurrentDraft =
  ApiSchemas['WorkflowDraftConflictCurrentDraft']

/** 生成的契约里 generation 必填（服务端现在总携带）；错误包装在字段缺失
 * 的响应（混合部署窗口）上降级为 null——下游按「未知代次」处理（不推进
 * CAS 基线），而不是假装 0。 */
export type ConflictCurrentDraft = Omit<
  WorkflowDraftConflictCurrentDraft,
  'generation'
> & { generation: number | null }

export class WorkflowDraftConflictError extends Error {
  readonly currentDraft: ConflictCurrentDraft

  constructor(detail: unknown) {
    super('workflow draft conflict')
    const payload =
      typeof detail === 'object' && detail !== null
        ? (detail as { current_draft?: unknown })
        : {}
    const current = (payload.current_draft ??
      {}) as Partial<WorkflowDraftConflictCurrentDraft>
    this.currentDraft = {
      definition_yaml: current.definition_yaml ?? null,
      updated_at: current.updated_at ?? null,
      definition_hash: current.definition_hash ?? null,
      generation: current.generation ?? null,
    }
    this.name = 'WorkflowDraftConflictError'
  }
}

/** 把 409 响应翻译成 WorkflowDraftConflictError，其它错误原样上抛（走既有
 * 的网络重试链）。 */
export async function wrapDraftConflict<T>(put: Promise<T>): Promise<T> {
  try {
    return await put
  } catch (error) {
    if ((error as Error & { status?: number })?.status === 409) {
      throw new WorkflowDraftConflictError(
        (error as Error & { detail?: unknown }).detail
      )
    }
    throw error
  }
}
