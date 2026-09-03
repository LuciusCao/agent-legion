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
   * 会 404，用户看到假失败 toast）。手动发布对话框不传，行为不变。 */
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

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
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
        <Button onClick={onCancel} variant="outlined">
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
