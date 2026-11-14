import type { WorkflowRevisionSummary } from '../../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export type Props = {
  open: boolean
  activeRevision: WorkflowRevisionSummary | null
  nextVersion: number
  createsRevision?: boolean
  definitionHash: string | null
  summary: ChangeSummaryViewModel | null
  onConfirm: () => void
  onCancel: () => void
  /** 提交进行中（#429 NIT：agent 请求确认期间禁用按钮防双击重放——第二击
   * 会 404，用户看到假失败 toast；#429 二轮复审 P3：confirming 期间关闭
   * 渠道也全部静默，见 requestClose）。手动发布对话框不传，行为不变。 */
  confirming?: boolean
  /** cancel 在途（#429 三轮复审 P3）：双击「返回编辑」或 cancel 在途按
   * ESC 会二次调 cancel → 404 → 红色假失败 toast。与 confirming 同款
   * 守卫，见 requestClose。手动发布对话框不传，行为不变。 */
  canceling?: boolean
  /** #542 发布面 advisory：agent 节点绑定的 skill 无机器契约（无
   * contract.yaml 且无嵌入块）时后端在读取侧现算的提示列表。仅呈现，
   * 不阻断发布；手动发布对话框不传，行为不变。 */
  advisories?: string[]
  /** #804 轮 4 P2-C 审 A 发 B：确认框打开期间草稿 YAML 被后台换掉 →
   * 禁确认 + 提示重审（审阅的 diff 与将发布的内容已不一致）。 */
  contentStale?: boolean
  /** Agent 请求确认以请求绑定的服务端草稿为准，本地 compare 尚未水合
   * 不构成否决；后端 draft_hash 与发布校验仍是权威门禁。手动发布保持默认 false。 */
  allowConfirmWithoutLocalChanges?: boolean
  /** M48：草稿里尚未经运行通道保存的 runtime 键（信息条）。 */
  runtimeChangedKeys?: string[]
  /** M48：本会话已经运行通道保存生效的 runtime 键（信息条）。 */
  runtimeSavedKeys?: string[]
}
