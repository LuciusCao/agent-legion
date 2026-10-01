import type { useWorkflowStudio } from '../shared/useWorkflowStudio'
import { WorkflowChangeSummaryPanel } from './WorkflowChangeSummaryPanel'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import styles from './WorkflowStudioChangesView.module.css'

type Studio = ReturnType<typeof useWorkflowStudio>

/** 变更视图实际消费的 studio 子集（右侧变更 Drawer 与发布前 review 共用）。 */
export type ChangesViewStudio = Pick<
  Studio,
  | 'validationMessage'
  | 'validationErrors'
  | 'compareErrors'
  | 'compareSummary'
  | 'compareState'
  | 'retryValidation'
>

// 变更 Drawer 的内容：校验结果 + 草稿对比摘要。
export function WorkflowStudioChangesView(props: {
  studio: ChangesViewStudio
  onSelectNode: (nodeKey: string) => void
}) {
  const { studio, onSelectNode } = props
  const hasValidation =
    studio.validationMessage !== '' ||
    studio.validationErrors.length > 0 ||
    (studio.compareErrors?.length ?? 0) > 0
  return (
    <div className={styles.checks}>
      <section aria-label="校验结果">
        <h3>校验结果</h3>
        {/* 轮 6 H3：传输失败终态（「校验失败：…」前缀）给显式重试——清空
            结果即触发自动校验重跑；结构失败（内容问题）重试无意义，不给。 */}
        {studio.validationMessage.startsWith('校验失败：') ? (
          <button
            type="button"
            className={styles.retryValidation}
            onClick={() => studio.retryValidation()}
          >
            重试校验
          </button>
        ) : null}
        {hasValidation ? (
          <WorkflowValidationPanel
            message={studio.validationMessage}
            errors={studio.validationErrors}
            compareErrors={studio.compareErrors ?? undefined}
            onSelectNode={onSelectNode}
          />
        ) : (
          <p className={styles.empty}>尚未运行校验。</p>
        )}
      </section>
      <WorkflowChangeSummaryPanel
        summary={studio.compareSummary}
        loading={studio.compareState === 'loading'}
        errors={studio.compareErrors}
        onSelectNode={onSelectNode}
      />
    </div>
  )
}
