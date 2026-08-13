import type { useWorkflowStudio } from './useWorkflowStudio'
import { WorkflowChangeSummaryPanel } from './components/WorkflowChangeSummaryPanel'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import styles from './WorkflowStudioGlobalDialog.module.css'

type Studio = ReturnType<typeof useWorkflowStudio>

// 全局对话框「变更与校验」模式的内容：校验结果 + 草稿对比摘要。
export function WorkflowStudioChangesView(props: {
  studio: Studio
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
