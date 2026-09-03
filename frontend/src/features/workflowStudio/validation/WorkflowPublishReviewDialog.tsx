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
  /** cancel 在途（#429 三轮复审 P3）：双击「返回编辑」或 cancel 在途按
   * ESC 会二次调 cancel → 404 → 红色假失败 toast。与 confirming 同款
   * 守卫，见 requestClose。手动发布对话框不传，行为不变。 */
  canceling?: boolean
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
  canceling = false,
}: Props) {
  const hasChanges = hasCompareSummaryChanges(summary)
  // 任一操作在途即禁止二次触发关闭（见下）。
  const resolving = confirming || canceling
  // #429 二轮复审 P3：confirm 进行中，关闭渠道（返回编辑/ESC/backdrop）
  // 全部不触发 cancel——发布已在途，此时 cancel 会让 revision 实际上线但
  // 回执/agent 状态显示被拒（误导）。回调早退一处守卫，覆盖三个入口。
  // 三轮复审 P3：cancel 在途同样静默——二次 cancel 必 404，红 toast 与
  // 正确回执同现是假失败。
  const requestClose = () => {
    if (!resolving) onCancel()
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
        <Button onClick={requestClose} variant="outlined" disabled={resolving}>
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
