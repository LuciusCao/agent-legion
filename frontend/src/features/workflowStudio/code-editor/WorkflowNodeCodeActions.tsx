import { Button } from '@mui/material'
import styles from './WorkflowNodeCodeSection.module.css'

// Action row for the node code card: fork (builtin) or edit/publish/reset
// (custom), plus「从模板新建」(no custom draft yet) and the version-history
// toggle. A pathless node with a draft gets a plain 编辑 entry. The reset to
// builtin is a two-step inline confirm.
export function WorkflowNodeCodeActions(props: {
  isCustom: boolean
  hasBuiltin: boolean
  hasDraft: boolean
  busy: boolean
  confirmingReset: boolean
  onEdit: () => void
  onCreateFromTemplate: () => void
  onPublish: () => void
  onToggleVersions: () => void
  onRequestReset: () => void
  onCancelReset: () => void
  onConfirmReset: () => void
}) {
  return (
    <div className={styles.actions}>
      {(props.isCustom || props.hasBuiltin || props.hasDraft) && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onEdit}
          disabled={props.busy}
        >
          {props.isCustom || !props.hasBuiltin ? '编辑' : 'fork 为自定义节点'}
        </Button>
      )}
      {!props.isCustom && !props.hasDraft && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onCreateFromTemplate}
          disabled={props.busy}
        >
          从模板新建
        </Button>
      )}
      {props.hasDraft && (
        <Button
          variant="outlined"
          size="small"
          onClick={props.onPublish}
          disabled={props.busy}
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
