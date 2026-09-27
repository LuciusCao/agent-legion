/**
 * 预览治理溢出菜单（#796 验收返工 R3，从 PreviewPanelHeader 拆出保体积
 * 预算）：治理动作收进 MoreVert 菜单而非一排裸按钮——预览此草稿（逐次
 * 授权）/ 发布草稿 / 恢复默认（归档破坏性动作，danger 色 + Divider 分隔）。
 * 菜单项的可用性由父级数据（draft/published/draftPreview）驱动，动作回调
 * 原样透传；这里不做任何授权/治理判断。
 */
import { useState } from 'react'
import { Divider, IconButton, Menu, MenuItem, Tooltip } from '@mui/material'
import { MoreVert } from '@mui/icons-material'
import type { PreviewPanelVersion } from './previewPanelApi'

export interface PreviewGovernanceMenuProps {
  draft: PreviewPanelVersion | null
  published: PreviewPanelVersion | null
  /** 草稿预览态（授权生效中）：预览项转为禁用的状态文案。 */
  draftPreview: boolean
  publishing: boolean
  onPreviewDraft: () => void
  onPublish: () => void
  onArchive: () => void
}

export function PreviewGovernanceMenu({
  draft,
  published,
  draftPreview,
  publishing,
  onPreviewDraft,
  onPublish,
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
          disabled={!draft || draftPreview}
          onClick={() => {
            close()
            onPreviewDraft()
          }}
        >
          {draftPreview ? '草稿预览中（左栏渲染中）' : '预览此草稿'}
        </MenuItem>
        <MenuItem
          disabled={!draft || publishing}
          onClick={() => {
            close()
            onPublish()
          }}
        >
          发布草稿
        </MenuItem>
        <Divider />
        <MenuItem
          disabled={!published && !draft}
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
