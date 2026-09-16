import { SyncOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'
import type { SharedMaterialDriftStatus } from '../../../api'
import {
  isRowPropagatable,
  type SharedMaterialFileRow,
} from './sharedMaterialsRows'
import styles from './WorkflowStudioSharedMaterialsDrawer.module.css'

export const DRIFT_LABELS: Record<SharedMaterialDriftStatus, string> = {
  synced: '一致',
  pending_sync: '待同步',
  missing_in_skill: '仓库缺失',
  skill_not_found: 'Skill 缺失',
}

const DRIFT_CHIP_CLASS: Record<SharedMaterialDriftStatus, string> = {
  synced: styles.chipSynced,
  pending_sync: styles.chipPending,
  missing_in_skill: styles.chipMissing,
  skill_not_found: styles.chipMissing,
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  return `${(size / 1024).toFixed(1)} KB`
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/**
 * One row of the merged file list: path label (click to view) + size/time +
 * inline drift badges of the mapped skills (compact, wrapping — long
 * skill names only grow this row). ``displayPath`` is the label inside the
 * row's group (material-dir groups drop their prefix); the full path is
 * still used for the viewer, the propagate action and the test id. Rows
 * for map sources missing from _shared carry a 缺失源 chip and no content
 * viewer / propagate action.
 */
export function SharedMaterialFileRowView({
  row,
  displayPath,
  propagating,
  onOpenFile,
  onPropagate,
}: {
  row: SharedMaterialFileRow
  displayPath: string
  propagating: boolean
  onOpenFile: (path: string) => void
  onPropagate: (row: SharedMaterialFileRow) => void
}) {
  const propagatable = isRowPropagatable(row)
  return (
    <li className={styles.listItem} data-testid={`shared-material-${row.path}`}>
      <div className={styles.rowTop}>
        {row.missingSource ? (
          <code className={styles.itemLabel}>{displayPath}</code>
        ) : (
          <button
            type="button"
            className={styles.fileButton}
            onClick={() => onOpenFile(row.path)}
          >
            <code className={styles.itemLabel}>{displayPath}</code>
          </button>
        )}
        {row.missingSource ? (
          <span className={`${styles.chip} ${styles.chipMissing}`}>缺失源</span>
        ) : (
          <>
            <span className={styles.chip}>{formatSize(row.size ?? 0)}</span>
            <span className={styles.chip}>
              {formatTime(row.modifiedAt ?? '')}
            </span>
          </>
        )}
        {propagatable && (
          <Tooltip title="同步并打 tag：把共享副本写入映射 skill 仓库，commit + 新 tag">
            <span>
              <IconButton
                size="small"
                aria-label={`同步 ${row.path}`}
                disabled={propagating}
                onClick={() => onPropagate(row)}
              >
                <SyncOutlined fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        )}
      </div>
      <div className={styles.skillChips}>
        {row.skills.length === 0 && !row.missingSource && (
          <span className={styles.chip}>未映射</span>
        )}
        {row.skills.map((entry) => (
          <span
            key={entry.skill}
            className={`${styles.chip} ${DRIFT_CHIP_CLASS[entry.status]}`}
            title={`${entry.skill}：${DRIFT_LABELS[entry.status]}`}
          >
            {entry.skill} · {DRIFT_LABELS[entry.status]}
          </span>
        ))}
      </div>
    </li>
  )
}
