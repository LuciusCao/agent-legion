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
  if (studio.canPublish) return null
  if (studio.compareState === 'loading') {
    return '正在与 active revision 对比，请稍候'
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
  return '编辑器当前内容不可提交'
}

/** 发布按钮：canPublish 为假时禁用并在 title 里说明原因（禁用按钮不触发
 * 自身 hover 事件，title 挂在外层 span 上）。无 Studio Provider（测试直渲
 * 染卡片）时不渲染。 */
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
