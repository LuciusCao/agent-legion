import { Button, Divider, Menu, MenuItem, Tooltip } from '@mui/material'
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
  /** 重置出口（#804 轮 4 P2-D 窄屏起步；#770 顶栏减法推广到全宽度）：
   * 低频破坏性动作不再外露为按钮，收进本菜单（window.confirm 确认）。 */
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
  // #770 顶栏减法：触发键只显示版本号，hash 这类只读信息降级到 tooltip
  // （与 aria-label 同源，读屏仍可得；完整列表在菜单项里）。
  const versionLabel = `v${currentVersion ?? '-'}`
  const detailLabel = `${versionLabel} · ${currentHash?.slice(0, 8) ?? '--------'}`

  function close() {
    setAnchorEl(null)
  }

  return (
    <div className={styles.revisionSelect}>
      <Tooltip title={`当前版本 ${detailLabel}（切换版本 / 重置草稿）`}>
        {/* disabled 时 Tooltip 需要 wrapper span（MUI 约定） */}
        <span>
          <Button
            size="small"
            variant="outlined"
            endIcon={<KeyboardArrowDownIcon fontSize="small" />}
            disabled={disabled || (revisions.length === 0 && !onResetDraft)}
            aria-label={`版本 ${detailLabel}`}
            aria-controls={open ? 'workflow-revision-menu' : undefined}
            aria-haspopup="menu"
            aria-expanded={open ? 'true' : undefined}
            onClick={(event) => setAnchorEl(event.currentTarget)}
          >
            {versionLabel}
          </Button>
        </span>
      </Tooltip>
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
        {onResetDraft && revisions.length > 0 ? <Divider /> : null}
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
