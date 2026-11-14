import { Alert } from '@mui/material'
import type { DraftSaveState } from './draftSaveTypes'

/** 保存、agent 建议与发布共用的持久错误详情；调用方决定显示哪些通道。 */
export function WorkflowStudioErrorDetails(props: {
  draftSave?: DraftSaveState
  runtimeError?: string | null
  publishError?: string | null
}) {
  return (
    <>
      {props.draftSave?.saveErrorMessage && (
        <Alert severity="error" role="alert">
          草稿保存失败：{props.draftSave.saveErrorMessage}
        </Alert>
      )}
      {props.runtimeError && (
        <Alert severity="error" role="alert">
          {props.runtimeError}
        </Alert>
      )}
      {props.publishError && (
        <Alert severity="error" role="alert">
          {props.publishError}
        </Alert>
      )}
    </>
  )
}
