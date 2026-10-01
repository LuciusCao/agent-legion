import { Button, Menu, MenuItem } from '@mui/material'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import { useState } from 'react'
import type { WorkflowRevisionSummary } from '../../../types'
import styles from './WorkflowRevisionSelect.module.css'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
  selectedRevisionId?: string | null
  currentVersion?: number
  currentHash?: string | null
  disabled?: boolean
  error?: string | null
  onSelectRevision: (revisionId: string) => void
  /** #804 轮 4 P2-D：窄屏重置出口——动作组的重置按钮窄屏隐藏（空间
   * 让给主按钮），重置收进本菜单（破坏性操作，window.confirm 确认）。 */
  onResetDraft?: () => void
}

export function WorkflowRevisionSelect({
  revisions,
  activeRevisionId,
  selectedRevisionId,
  currentVersion,
  currentHash,
  disabled,
  error,
  onSelectRevision,
  onResetDraft,
}: Props) {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const open = Boolean(anchorEl)
  const currentLabel = `v${currentVersion ?? '-'} · ${currentHash?.slice(0, 8) ?? '--------'}`

  function close() {
    setAnchorEl(null)
  }

  return (
    <div className={styles.revisionSelect}>
      <Button
        size="small"
        variant="outlined"
        endIcon={<KeyboardArrowDownIcon fontSize="small" />}
        disabled={disabled || (revisions.length === 0 && !onResetDraft)}
        aria-controls={open ? 'workflow-revision-menu' : undefined}
        aria-haspopup="menu"
        aria-expanded={open ? 'true' : undefined}
        onClick={(event) => setAnchorEl(event.currentTarget)}
      >
        {currentLabel}
      </Button>
      <Menu
        id="workflow-revision-menu"
        anchorEl={anchorEl}
        open={open}
        onClose={close}
        MenuListProps={{ 'aria-label': 'Workflow revisions' }}
      >
        {error && <MenuItem disabled>加载失败：{error}</MenuItem>}
        {onResetDraft ? (
          <MenuItem
            onClick={() => {
              close()
              // 破坏性操作必须确认（轮 4 P2-D）：丢弃未发布变更。
              if (
                window.confirm('丢弃当前草稿的未发布变更，重置为已发布版本？')
              )
                onResetDraft()
            }}
          >
            重置为已发布版本
          </MenuItem>
        ) : null}
        {revisions.map((revision) => {
          const active = revision.id === activeRevisionId
          const selected = revision.id === selectedRevisionId
          return (
            <MenuItem
              key={revision.id}
              selected={selected}
              onClick={() => {
                onSelectRevision(revision.id)
                close()
              }}
            >
              <span className={styles.version}>v{revision.version}</span>
              <span className={styles.status}>
                {active ? 'active' : revision.status}
              </span>
              <span className={styles.hash}>
                {revision.definition_hash.slice(0, 8)}
              </span>
            </MenuItem>
          )
        })}
      </Menu>
    </div>
  )
}
