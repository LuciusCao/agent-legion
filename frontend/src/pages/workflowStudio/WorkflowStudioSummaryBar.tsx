import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
type Props = {
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
}
export function WorkflowStudioSummaryBar({
  workflow,
  revision,
  dirty,
  actionState,
  canSubmit,
  onValidate,
  onPublish,
  onReset,
}: Props) {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  const busy = actionState !== 'idle'
  return (
    <section aria-label="Workflow summary">
      <div>
        <h1>{workflow?.label ?? '工作流'}</h1>
        <span>{workflow?.key ?? '未加载'}</span>
      </div>
      <div>
        <span>{revision ? `v${revision.version}` : '无 active revision'}</span>
        <span>{hash}</span>
        <span>{dirty ? '有未保存修改' : '已同步'}</span>
      </div>
      <div>
        <button
          type="button"
          onClick={onValidate}
          disabled={!canSubmit || busy}
        >
          {actionState === 'validating' ? '校验中' : '校验'}
        </button>
        <button type="button" onClick={onPublish} disabled={!canSubmit || busy}>
          {actionState === 'publishing' ? '发布中' : '发布'}
        </button>
        <button type="button" onClick={onReset} disabled={!dirty || busy}>
          重置为当前版本
        </button>
      </div>
    </section>
  )
}
