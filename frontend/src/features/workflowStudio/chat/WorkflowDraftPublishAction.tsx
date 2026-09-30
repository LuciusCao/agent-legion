import {
  useStudioStateOptional,
  type StudioState,
} from '../shared/studioStateContext'
import styles from './StudioChatPanel.module.css'

/* #667 B1：聊天草稿卡内的发布入口。发布对象永远是编辑器当前 YAML——
 * requestPublish 走既有 canPublish 门控（dirty + compare 有变更 + 无
 * yaml/schema 阻断）并打开 WorkflowPublishReviewDialog，与顶栏命令条共用
 * 同一发布管道，无需跳画布。卡片里的草稿文本只是 agent 产出物，应用进
 * 编辑器后才参与发布。 */

function publishDisabledReason(studio: StudioState): string | null {
  // canPublish 不含在途态：确认框关闭后首个 publish POST 仍在途时，按钮
  // 必须保持禁用，否则可再开确认框发起第二个 POST（重复 revision / 版本
  // 冲突）。codex 轮 5 P1：发布/校验是独立在途维度，分别给原因。
  if (studio.publishing) return '发布进行中，请稍候'
  if (studio.validating) return '校验进行中，请稍候'
  if (studio.canPublish) return null
  if (studio.compareState === 'loading') {
    return '正在与 active revision 对比，请稍候'
  }
  // 轮 6 H4：compare 传输失败要给出真实原因（否则落到「没有可发布的
  // 变更」是误导——实际上是对比没跑成）。
  if (studio.compareState === 'error') {
    return '草稿对比失败，请在画布上的警示处重试'
  }
  const hasBlockingError = (studio.compareErrors ?? []).some(
    (error) => error.category === 'yaml' || error.category === 'schema'
  )
  if (hasBlockingError) return 'YAML / 结构存在错误，请先修正再发布'
  // 与 useWorkflowStudioActions 的 hasCompareChanges 口径保持一致（不计
  // metadataChanges）。
  const summary = studio.compareSummary
  const hasChanges = Boolean(
    summary &&
    (summary.nodeChanges.length ||
      summary.edgeChanges.length ||
      summary.intakeChanges.length ||
      summary.riskFlags.length)
  )
  if (!hasChanges) return '与 active revision 没有可发布的变更'
  // 轮 6 H2：冲突未解决禁发布（canPublish 已含此门控）。
  if (studio.draftSave?.conflict) return '草稿存在冲突，请先解决冲突再发布'
  // codex 轮 3 P2：canPublish 已绑定「当前 YAML 校验通过」（自动校验驱动），
  // 未通过时给出校验侧原因而不是笼统的不可提交。
  if (studio.validationMessage?.startsWith('校验失败'))
    return '校验失败，请修复后再发布'
  if (studio.validationMessage !== '校验通过') return '草稿校验通过后才能发布'
  return '编辑器当前内容不可提交'
}

/** 发布按钮：发布/校验在途或 canPublish 为假时禁用并在 title 里说
 * 明原因（禁用按钮不触发自身 hover 事件，title 挂在外层 span 上）。无
 * Studio Provider（测试直渲染卡片）时不渲染。 */
export function WorkflowDraftPublishButton() {
  const studio = useStudioStateOptional()
  if (!studio) return null
  const disabledReason = publishDisabledReason(studio)
  const label = studio.createsRevision === false ? '保存运行配置' : '发布新版本'
  return (
    <span title={disabledReason ?? undefined}>
      <button
        type="button"
        className={styles.draftButton}
        disabled={disabledReason !== null}
        onClick={() => studio.requestPublish()}
      >
        {label}
      </button>
    </span>
  )
}

/** 冲突提示：草稿卡 YAML 与编辑器当前内容不一致时（用户有未保存的本地
 * 编辑导致服务端草稿被挂起，或应用草稿后又改过），发布审的是编辑器里的
 * YAML。参考 AgentPublishRequestDialog 的 flush-first 模式只给提示，不
 * 强行 flush。 */
export function WorkflowDraftStaleHint({ draftYaml }: { draftYaml: string }) {
  const studio = useStudioStateOptional()
  if (!studio || studio.definitionYaml === draftYaml) return null
  return (
    <div className={styles.draftHint} role="note">
      该草稿与编辑器当前内容不一致，发布将以编辑器中的 YAML 为准
    </div>
  )
}
