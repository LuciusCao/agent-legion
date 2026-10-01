/**
 * Studio 右岛「共享材料」触发按钮（issue #643）：只负责置位开合状态，抽屉
 * 本体由 WorkflowStudioSharedMaterialsDrawer 在 SplitLayout 层渲染（#812
 * 对抗轮 D1——岛的 backdrop-filter 会捕获 fixed paper 为包含块，抽屉
 * 不能挂在岛内；按钮留在岛内，与本体经 StudioViewContext 共享开合状态）。
 */
import { FolderSharedOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'
import { useStudioView } from './studioStateContext'

export function WorkflowStudioSharedMaterialsButton() {
  // 轮 9 P2：开合状态提升到 StudioViewContext（Dock 避让需要感知抽屉）。
  const view = useStudioView()
  const { setMaterialsOpen: setOpen } = view
  return (
    <Tooltip title="Skill 共享材料">
      <IconButton
        size="small"
        aria-label="Skill 共享材料"
        onClick={() => setOpen(true)}
        sx={{ borderRadius: '8px', gap: '4px', padding: '4px 8px' }}
      >
        <FolderSharedOutlined fontSize="small" />
        {/* #799 精修：右岛图标+文字并排；窄屏由岛 CSS 隐藏文字只留图标 */}
        <span className="studio-island-text">共享素材</span>
      </IconButton>
    </Tooltip>
  )
}
