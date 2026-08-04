import { Button } from '@mui/material'
import styles from './WorkflowNodeCodeSection.module.css'

// Action row for the node code card: fork (builtin) or edit/publish/reset
// (custom), plus the version-history toggle. The reset to builtin is a
// two-step inline confirm.
export function WorkflowNodeCodeActions(props: {
  isCustom: boolean
  hasDraft: boolean
  busy: boolean
  confirmingReset: boolean
  onEdit: () => void
  onPublish: () => void
  onToggleVersions: () => void
  onRequestReset: () => void
  onCancelReset: () => void
  onConfirmReset: () => void
}) {
  return (
    <div className={styles.actions}>
      {!props.isCustom && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onEdit}
          disabled={props.busy}
        >
          fork 为自定义节点
        </Button>
      )}
      {props.isCustom && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onEdit}
          disabled={props.busy}
        >
          编辑
        </Button>
      )}
      {props.isCustom && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onPublish}
          disabled={props.busy || !props.hasDraft}
        >
          发布
        </Button>
      )}
      <Button
        variant="text"
        size="small"
        onClick={props.onToggleVersions}
        disabled={props.busy}
      >
        版本历史
      </Button>
      {props.isCustom &&
        (props.confirmingReset ? (
          <>
            <Button
              variant="outlined"
              size="small"
              color="error"
              onClick={props.onConfirmReset}
              disabled={props.busy}
            >
              确认回落内置
            </Button>
            <Button
              variant="text"
              size="small"
              onClick={props.onCancelReset}
              disabled={props.busy}
            >
              取消
            </Button>
          </>
        ) : (
          <Button
            variant="text"
            size="small"
            color="error"
            onClick={props.onRequestReset}
            disabled={props.busy}
          >
            回落内置
          </Button>
        ))}
    </div>
  )
}
