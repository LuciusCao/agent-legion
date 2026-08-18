import type { useWorkflowStudio } from './useWorkflowStudio'
import { WorkflowChangeSummaryPanel } from './components/WorkflowChangeSummaryPanel'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import styles from './WorkflowStudioChangesView.module.css'

type Studio = ReturnType<typeof useWorkflowStudio>

/** 变更视图实际消费的 studio 子集（画布模式与历史全局弹窗共用）。 */
export type ChangesViewStudio = Pick<
  Studio,
  | 'validationMessage'
  | 'validationErrors'
  | 'compareErrors'
  | 'compareSummary'
  | 'compareState'
>

// 画布「变更」模式的内容：校验结果 + 草稿对比摘要。
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
