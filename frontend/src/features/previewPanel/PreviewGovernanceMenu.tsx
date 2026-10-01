/**
 * 预览治理溢出菜单（#796 验收返工 R3 建、R4 收敛）：R4 起「预览此草稿 /
 * 发布草稿」外露出头部的治理区（状态 Chip 旁的小按钮组），菜单里只剩
 * 「恢复默认」——归档是破坏性治理动作（已发布版本与草稿都被归档、全员
 * 生效），收拢在 ⋮ 里并用 danger 色 + 确认弹窗做视觉/操作双重隔离。菜单
 * 骨架保留（后续可能加项）。
 */
import { useState } from 'react'
import { IconButton, Menu, MenuItem, Tooltip } from '@mui/material'
import { MoreVert } from '@mui/icons-material'

export interface PreviewGovernanceMenuProps {
  /** 有已发布版本或草稿时才可归档。 */
  canArchive: boolean
  onArchive: () => void
}

export function PreviewGovernanceMenu({
  canArchive,
  onArchive,
}: PreviewGovernanceMenuProps) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const close = () => setAnchor(null)
  return (
    <>
      <Tooltip title="预览治理操作">
        <IconButton
          size="small"
          aria-label="预览治理操作"
          aria-haspopup="menu"
          aria-expanded={anchor !== null}
          onClick={(event) => setAnchor(event.currentTarget)}
        >
          <MoreVert fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={close}>
        <MenuItem
          disabled={!canArchive}
          sx={{ color: 'error.main' }}
          onClick={() => {
            close()
            if (window.confirm('恢复默认预览？已发布版本与草稿都会被归档。')) {
              onArchive()
            }
          }}
        >
          恢复默认（归档）
        </MenuItem>
      </Menu>
    </>
  )
}
