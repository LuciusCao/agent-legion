import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { SharedMaterialFileRow } from './sharedMaterialsRows'
import { collectPropagateImpact } from './sharedMaterialsRows'
import styles from './WorkflowStudioSharedMaterialsDrawer.module.css'

/**
 * 传播轻确认（#673）：写明逐 skill commit + 新 tag、跳过已一致、失败
 * 隔离、不动 DB 锁/pin；pending 时禁取消。#683 review P2-1：后端按 skill
 * 粒度传播——``sources`` 只筛选哪些 skill 运行，每个运行中的 skill 在同
 * 一次 commit 里同步它映射的整个共享材料集合，所以确认前列出将被写入
 * 的全部文件（含映射到同批 skill 的其他共享材料），而不是只说当前行。
 */
export function SharedMaterialsPropagateConfirmDialog({
  row,
  rows,
  pending,
  onCancel,
  onConfirm,
}: {
  row: SharedMaterialFileRow
  /** 全部清单行（含缺失源合成行）：推导完整影响范围的数据源。 */
  rows: SharedMaterialFileRow[]
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const impact = collectPropagateImpact(row, rows)
  return (
    <Dialog open onClose={() => !pending && onCancel()}>
      <DialogTitle>同步并打 tag</DialogTitle>
      <DialogContent>
        将把以下 {impact.files.length} 个文件同步进 {impact.skills.length}{' '}
        个映射 skill 的仓库（含这些 skill 映射的其他共享材料，随同一次 commit
        写入）：
        <ul className={styles.impactList} data-testid="propagate-impact-files">
          {impact.files.map((file) => (
            <li key={file.path}>
              <code>{file.path}</code>
              {file.requested ? '（本次所选）' : '（同 skill 的其他共享材料）'}
            </li>
          ))}
        </ul>
        逐 skill 写入相同相对路径、commit 并打新 patch tag（最高版本
        +0.0.1；无版本 tag 的仓库从 v0.1.0 起）。已一致的 skill 会跳过，单个
        skill 失败不影响其它 skill；DB 版本锁与节点 pin 不变。
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
