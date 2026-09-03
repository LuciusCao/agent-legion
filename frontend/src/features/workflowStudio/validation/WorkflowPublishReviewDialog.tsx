import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { WorkflowRevisionSummary } from '../../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { WorkflowPublishReviewDialogChanges } from './WorkflowPublishReviewDialogChanges'
import { hasCompareSummaryChanges } from './WorkflowPublishReviewDialog.helpers'
import { WorkflowPublishReviewDialogMeta } from './WorkflowPublishReviewDialogMeta'

type Props = {
  open: boolean
  workflowKey: string | null
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
}

export function WorkflowPublishReviewDialog({
  open,
  workflowKey,
  activeRevision,
  nextVersion,
  createsRevision = true,
  definitionHash,
  summary,
  onConfirm,
  onCancel,
  confirming = false,
}: Props) {
  const hasChanges = hasCompareSummaryChanges(summary)
  // #429 二轮复审 P3：confirm 进行中，关闭渠道（返回编辑/ESC/backdrop）
  // 全部不触发 cancel——发布已在途，此时 cancel 会让 revision 实际上线但
  // 回执/agent 状态显示被拒（误导）。回调早退一处守卫，覆盖三个入口。
  const requestClose = () => {
    if (confirming) return
    onCancel()
  }

  return (
    <Dialog open={open} onClose={requestClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {createsRevision ? '发布 workflow revision' : '保存节点运行配置'}
      </DialogTitle>
      <DialogContent>
        <WorkflowPublishReviewDialogMeta
          workflowKey={workflowKey}
          activeRevision={activeRevision}
          nextVersion={nextVersion}
          createsRevision={createsRevision}
          definitionHash={definitionHash}
        />
        <WorkflowPublishReviewDialogChanges summary={summary} />
      </DialogContent>
      <DialogActions>
        <Button onClick={requestClose} variant="outlined" disabled={confirming}>
          返回编辑
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="primary"
          disabled={!hasChanges || confirming}
        >
          {createsRevision ? '确认发布' : '确认保存'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
