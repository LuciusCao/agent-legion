/** 自动校验的传输失败退避重试（codex 轮 4 P1-1，从 useDraftAutoValidation
 * 拆出保体积预算）：validate resolve（{valid:false}，YAML/schema 结构
 * 问题）直接返回不重试；reject（网络/5xx 传输失败）按 2s/4s/8s 退避至多
 * 重试 3 次，耗尽后把最后一次错误抛给调用方写终态。退避等待期间内容
 * 已变（isStale）则提前中止，由新一轮校验接管。 */
import { validateWorkflowDraft } from '../../../api'

/** 传输失败至多自动重试 3 次（指数退避 2s/4s/8s）。 */
export const MAX_TRANSPORT_RETRIES = 3
const RETRY_BASE_MS = 2000

export async function validateDraftWithRetry(
  workspaceId: string,
  yaml: string,
  isStale: () => boolean
): Promise<{ valid: boolean; errors: string[] }> {
  for (let attempt = 0; ; attempt++) {
    if (isStale()) throw new DraftValidationStaleError()
    try {
      const result = await validateWorkflowDraft(workspaceId, yaml)
      // 成功路径同样要过期检查：await 期间内容已变，结果即作废。
      if (isStale()) throw new DraftValidationStaleError()
      return result
    } catch (e) {
      if (isStale()) throw new DraftValidationStaleError()
      if (attempt >= MAX_TRANSPORT_RETRIES) throw e
      await new Promise((resolve) =>
        setTimeout(resolve, RETRY_BASE_MS * 2 ** attempt)
      )
    }
  }
}

/** 内容在校验/退避期间已变——本轮结果作废（调用方静默丢弃）。 */
export class DraftValidationStaleError extends Error {}
