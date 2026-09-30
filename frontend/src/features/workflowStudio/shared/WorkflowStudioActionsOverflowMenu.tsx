/**
 * 指挥中心岛的 ⋮ 溢出菜单（#799 精修，从 WorkflowStudioCommandBarActions
 * 拆出保体积预算）：低频破坏性动作「重置」收进菜单——PreviewGovernanceMenu
 * 同款模式（aria-label「更多操作」+ Menu/MenuItem）。
 */
import { useState } from 'react'
import { IconButton, Menu, MenuItem, Tooltip } from '@mui/material'
import { MoreVert } from '@mui/icons-material'

export function WorkflowStudioActionsOverflowMenu(props: {
  disabled: boolean
  onReset: () => void
}) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const closeMenu = () => setMenuAnchor(null)
  return (
    <>
      <Tooltip title="更多操作">
        <IconButton
          size="small"
          aria-label="更多操作"
          aria-haspopup="menu"
          aria-expanded={menuAnchor !== null}
          onClick={(event) => setMenuAnchor(event.currentTarget)}
        >
          <MoreVert fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={menuAnchor}
        open={menuAnchor !== null}
        onClose={closeMenu}
      >
        <MenuItem
          disabled={props.disabled}
          onClick={() => {
            closeMenu()
            props.onReset()
          }}
        >
          重置
        </MenuItem>
      </Menu>
    </>
  )
}
