import { Fullscreen } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'

export function WorkflowDagFullscreenButton({
  onClick,
}: {
  onClick: () => void
}) {
  return (
    <Tooltip title="全屏查看 DAG">
      <IconButton
        size="small"
        onClick={onClick}
        aria-label="open fullscreen DAG"
      >
        <Fullscreen />
      </IconButton>
    </Tooltip>
  )
}
