/**
 * 产物预览勾选菜单（issue #11 第 3 层）：列出 job 的 artifacts，
 * 勾选状态 = !hidden.includes(name)，点击写 workspace 级配置。
 * 拆出 ArtifactPreviewPanel 以过架构文件预算。
 */
import { useState } from 'react'
import { Checkbox, ListItemText, Menu, MenuItem } from '@mui/material'
import { useWorkspacePreviewConfig } from '../../hooks/useWorkspacePreviewConfig'
import styles from './ArtifactPreviewConfigMenu.module.css'

export interface ArtifactPreviewConfigMenuProps {
  workspaceId: string | undefined
  artifacts: string[]
}

export function ArtifactPreviewConfigMenu({
  workspaceId,
  artifacts,
}: ArtifactPreviewConfigMenuProps) {
  const { previewHidden, toggleArtifact } = useWorkspacePreviewConfig(workspaceId)
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const menuOpen = Boolean(menuAnchor)
  const hiddenSet = new Set(previewHidden)

  if (artifacts.length === 0) return null

  return (
    <>
      <button
        type="button"
        className={styles.configButton}
        aria-label="配置预览产物"
        onClick={(event) => setMenuAnchor(event.currentTarget)}
      >
        选择显示的产物
      </button>
      <Menu
        anchorEl={menuAnchor}
        open={menuOpen}
        onClose={() => setMenuAnchor(null)}
        slotProps={{ paper: { sx: { maxHeight: 360, minWidth: 280 } } }}
      >
        {artifacts.map((name) => {
          const isChecked = !hiddenSet.has(name)
          return (
            <MenuItem
              key={name}
              dense
              onClick={() => void toggleArtifact(name, !isChecked)}
            >
              <Checkbox checked={isChecked} tabIndex={-1} disableRipple size="small" />
              <ListItemText primary={name} />
            </MenuItem>
          )
        })}
      </Menu>
    </>
  )
}
