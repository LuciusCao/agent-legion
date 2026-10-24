/** 自动校验的传输失败退避重试（codex 轮 4 P1-1，从 useDraftAutoValidation
 * 拆出保体积预算）：validate resolve（{valid:false}，YAML/schema 结构
 * 问题）直接返回不重试；reject 里 4xx 是确定性请求侧拒绝（#1359，
 * #1196 同族——与 draftSaveQueue 同口径），直接终结不重试；5xx/网络错
 * 按 2s/4s/8s 退避至多重试 3 次，耗尽后把最后一次错误抛给调用方写终态。
 * 退避等待期间内容已变（isStale）则提前中止，由新一轮校验接管。 */
import { validateWorkflowDraft } from '../../../api'

/** 传输失败至多自动重试 3 次（指数退避 2s/4s/8s）。 */
export const MAX_TRANSPORT_RETRIES = 3
const RETRY_BASE_MS = 2000

/** #1360：校验触发延迟——给「未发布变更」瞬态 chip 一个可渲染窗口
 * （与 compare debounce 同量级；语义见 useDraftAutoValidation 文件头）。 */
export const VALIDATION_TRIGGER_DELAY_MS = 400

/** 一次校验运行的落定接线（从 useDraftAutoValidation 拆出保体积预算）：
 * 结果/终态错误写 report；StaleError 静默吞（迟到结果作废）；
 * finally 只复位最新一次运行的 validating（onSettle 由调用方带 runId 守卫）。 */
export function settleDraftValidation(
  promise: Promise<{ valid: boolean; errors: string[] }>,
  report: (errors: string[], message: string) => void,
  onSettle: () => void
): void {
  void promise
    .then((result) =>
      report(result.errors, result.valid ? '校验通过' : '校验失败')
    )
    .catch((e: unknown) => {
      if (e instanceof DraftValidationStaleError) return
      report([], `校验失败：${(e instanceof Error && e.message) || '网络错误'}`)
    })
    .finally(onSettle)
}

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
      // #1359（#1196 同族遗漏）：4xx 是确定性的请求侧拒绝（401/403/404/422
      // 等），退避重试只会延后同一失败——直接终结，与 draftSaveQueue 的
      // terminal 口径一致；仅 5xx/网络错误（无 status）走退避。
      const status = (e as { status?: unknown } | null)?.status
      if (typeof status === 'number' && status >= 400 && status < 500) throw e
      if (attempt >= MAX_TRANSPORT_RETRIES) throw e
      await new Promise((resolve) =>
        setTimeout(resolve, RETRY_BASE_MS * 2 ** attempt)
      )
    }
  }
}

/** 内容在校验/退避期间已变——本轮结果作废（调用方静默丢弃）。 */
export class DraftValidationStaleError extends Error {}
