/** #633：workflow 草稿 PUT 的 CAS 失败（409）——服务端草稿已被 agent/其它
 * 会话推进，detail 携带当前草稿；编辑器据此提示用户而非静默覆盖。
 * 从 workflowDraft.ts 拆出（文件体积预算）。 */

export type WorkflowDraftConflictDetail = {
  definition_yaml: string | null
  updated_at: string | null
}

export class WorkflowDraftConflictError extends Error {
  readonly currentDraft: WorkflowDraftConflictDetail

  constructor(detail: unknown) {
    super('workflow draft conflict')
    const payload =
      typeof detail === 'object' && detail !== null
        ? (detail as { current_draft?: unknown })
        : {}
    const current = (payload.current_draft ??
      {}) as Partial<WorkflowDraftConflictDetail>
    this.currentDraft = {
      definition_yaml: current.definition_yaml ?? null,
      updated_at: current.updated_at ?? null,
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
