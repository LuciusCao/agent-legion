import { fetchWorkflowDraft } from '../../../api/workflowDraft'
import type { UiState } from '../../../stores/uiStore'
import type { StudioState } from './studioStateContext'
import type { AgentPublishRequestState } from './useAgentPublishRequest'

/** #429 三轮 P1-3（审阅-确认一致性）：画布草稿 800ms debounce 自动保存，
 * 点确认时最新编辑可能尚未落盘——onConfirm 先 flush（取消 debounce 直接
 * PUT 本页草稿）、重读服务端草稿，再调确认端点，保证确认端点读到的
 * workspace_workflow_drafts 与弹窗 compare summary 是同一份。
 * #429 四轮 P2-1（完成序，非先发优势）：flush 的 PUT promise 被 await，
 * read-draft 在 PUT resolve 之后才发出——旧实现 flush 不等待，若 PUT 迟于
 * confirm 的服务端读落地，会发布旧草稿且 hash 恰好匹配（用户看的是新
 * 版、发布的是旧版）。read-draft 仍只作重读校准（结果丢弃）；flush/重读
 * 失败时提示错误并中止 confirm（不发布未确认落盘的内容），后端 hash 校验
 * 仍是最终兜底。
 * #429 终局 P2-2（flush 失败的真实形态）：DraftSaveController.flushNow 全
 * 路径 resolve 不 reject——保存失败只进 state（status='error'），旧守卫的
 * catch 永远等不到 flush 的 reject，是死代码。失败的真实形态是 resolve-
 * but-failed：flush 返回后必须查终态，error 即中止 confirm（重读草稿读到
 * 的仍是旧值，发布的会是用户没审过的旧草稿——hash 只闭合「服务端≠请求」
 * 方向，挡不住这条）。
 * #429 收尾 P2-1（终态的读法）：上一轮查的是 studio.draftSave.status——
 * 那是 React useState 快照链路（controller.subscribe → setState），
 * confirmAgentRequest 闭包捕获的是点击那一刻的 state 对象，await flush
 * 期间落定的 error 态不会出现在闭包里——守卫只对「点击前已 error」生效，
 * 「本次 flush 的 PUT 在 await 期间失败」会漏过（发布旧草稿，且请求 hash
 * 绑的就是旧草稿、后端 hash 挡不住）。现在 flushDraftSave 的 resolve 值
 * 携带本次 flush 的终态（DraftSaveFlushResult，controller 的 live 状态），
 * 守卫读 result.ok。 */
export async function confirmAgentPublishRequest({
  workspaceId,
  studio,
  agentRequest,
  showToast,
}: {
  workspaceId: string | undefined
  studio: Pick<
    StudioState,
    'flushDraftSave' | 'definitionYaml' | 'markDraftPublished'
  >
  agentRequest: Pick<AgentPublishRequestState, 'confirm'>
  showToast: UiState['showToast']
}): Promise<boolean> {
  if (!workspaceId) return false
  let publishedYaml: string
  try {
    const flushed = await studio.flushDraftSave?.()
    if (flushed && !flushed.ok) throw new Error('draft save failed')
    const reread = await fetchWorkflowDraft(workspaceId)
    // #1122：登记确认端点实际将发布的服务端原文；草稿行已消失时回落画布
    // 原文，confirm 会失败，登记无害。
    publishedYaml = reread.definition_yaml ?? studio.definitionYaml
  } catch {
    showToast('草稿尚未保存成功，请稍后重试确认', 'error')
    return false
  }
  // onConfirmed 必须在 confirm 内部 invalidate 重取之前登记发布原文。
  await agentRequest.confirm(() => studio.markDraftPublished(publishedYaml))
  return true
}
