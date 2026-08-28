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
        disabled={disabled || revisions.length === 0}
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
