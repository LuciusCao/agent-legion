/**
 * 产物预览勾选菜单（issue #11 第 3 层）：列出 job 的 artifacts 与勾选态，
 * 纯展示——勾选态 = 不在全量隐藏名单（workspace 配置 ∪ 结构化去重，
 * #255），点击统一回调给调用方（普通产物写 workspace 配置，结构化
 * 消费产物走会话态恢复，路由在 ArtifactPreviewPanel）。
 * 拆出 ArtifactPreviewPanel 以过架构文件预算。
 */
import { useState } from 'react'
import { Checkbox, ListItemText, Menu, MenuItem } from '@mui/material'
import styles from './ArtifactPreviewConfigMenu.module.css'

export interface ArtifactPreviewConfigMenuProps {
  artifacts: string[]
  /** 全量隐藏名单（workspace 配置 ∪ 结构化去重）：不在其中 = 勾选。 */
  hiddenNames: ReadonlySet<string>
  onToggle: (name: string, visible: boolean) => void
}

export function ArtifactPreviewConfigMenu({
  artifacts,
  hiddenNames,
  onToggle,
}: ArtifactPreviewConfigMenuProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const menuOpen = Boolean(menuAnchor)

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
          const isChecked = !hiddenNames.has(name)
          return (
            <MenuItem
              key={name}
              dense
              onClick={() => onToggle(name, !isChecked)}
            >
              <Checkbox
                checked={isChecked}
                tabIndex={-1}
                disableRipple
                size="small"
              />
              <ListItemText primary={name} />
            </MenuItem>
          )
        })}
      </Menu>
    </>
  )
}
