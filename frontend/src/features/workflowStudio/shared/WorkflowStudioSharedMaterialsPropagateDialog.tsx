import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { SharedMaterialFileRow } from './sharedMaterialsRows'

/** 传播轻确认（#673）：写明逐 skill commit + 新 tag、跳过已一致、失败
 * 隔离、不动 DB 锁/pin；pending 时禁取消。 */
export function SharedMaterialsPropagateConfirmDialog({
  row,
  pending,
  onCancel,
  onConfirm,
}: {
  row: SharedMaterialFileRow
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Dialog open onClose={() => !pending && onCancel()}>
      <DialogTitle>同步并打 tag</DialogTitle>
      <DialogContent>
        将把 <code>{row.path}</code> 的共享副本同步进 {row.skills.length} 个映射
        skill 的仓库：逐 skill 写入相同相对路径、commit 并打新 tag（最新版本
        +0.0.1）。已一致的 skill 会跳过，单个 skill 失败不影响其它 skill；DB
        版本锁与节点 pin 不变。
      </DialogContent>
      <DialogActions>
        <Button variant="text" disabled={pending} onClick={onCancel}>
          取消
        </Button>
        <Button variant="contained" disabled={pending} onClick={onConfirm}>
          {pending ? '同步中…' : '同步并打 tag'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
