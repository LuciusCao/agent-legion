import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { WorkflowPublishReviewDialogAdvisories } from './WorkflowPublishReviewDialogAdvisories'
import { WorkflowPublishReviewDialogChanges } from './WorkflowPublishReviewDialogChanges'
import { canConfirmPublish } from './WorkflowPublishReviewDialog.helpers'
import { WorkflowPublishReviewDialogMeta } from './WorkflowPublishReviewDialogMeta'
import type { Props } from './WorkflowPublishReviewDialog.types'

export function WorkflowPublishReviewDialog({
  open,
  activeRevision,
  nextVersion,
  createsRevision = true,
  definitionHash,
  summary,
  onConfirm,
  onCancel,
  confirming = false,
  canceling = false,
  advisories = [],
  contentStale = false,
  allowConfirmWithoutLocalChanges = false,
  runtimeChangedKeys = [],
  runtimeSavedKeys = [],
}: Props) {
  const resolving = confirming || canceling
  // 任一操作在途即禁止二次触发关闭与确认（#429 四轮 codex P2：cancel 在途
  // 时确认按钮不禁会并发 cancel+confirm，终态由后端竞态决定）。
  // #429 二轮复审 P3：confirm 进行中，关闭渠道（返回编辑/ESC/backdrop）
  // 全部不触发 cancel——发布已在途，此时 cancel 会让 revision 实际上线但
  // 回执/agent 状态显示被拒（误导）。回调早退一处守卫，覆盖三个入口。
  // 三轮复审 P3：cancel 在途同样静默——二次 cancel 必 404，红 toast 与
  // 正确回执同现是假失败。
  // M49：本地确认门禁收口到 canConfirmPublish（结构 diff 或待生效 runtime
  // 键任一即可确认）。#1198 仅让 Agent 请求跳过这道冗余本地门。
  const confirmDisabled =
    (!allowConfirmWithoutLocalChanges &&
      !canConfirmPublish(summary, runtimeChangedKeys)) ||
    resolving ||
    contentStale
  const requestClose = () => (resolving ? undefined : onCancel())

  return (
    <Dialog open={open} onClose={requestClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {createsRevision ? '发布 workflow revision' : '保存节点运行配置'}
      </DialogTitle>
      <DialogContent>
        <WorkflowPublishReviewDialogMeta
          activeRevision={activeRevision}
          nextVersion={nextVersion}
          createsRevision={createsRevision}
          definitionHash={definitionHash}
        />
        <WorkflowPublishReviewDialogChanges
          summary={summary}
          createsRevision={createsRevision}
          runtimeChangedKeys={runtimeChangedKeys}
          runtimeSavedKeys={runtimeSavedKeys}
        />
        <WorkflowPublishReviewDialogAdvisories
          advisories={advisories}
          contentStale={contentStale}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={requestClose} variant="outlined" disabled={resolving}>
          返回编辑
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="primary"
          disabled={confirmDisabled}
        >
          {createsRevision ? '确认发布' : '确认保存'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
