/** 仅返回按钮的最小岛（#799 codex 复核 P2：loading/error 分支不挂
 * Workspace、双岛不渲染，AppBar 已移除——返回入口必须在加载/失败态
 * 也可用，挂在 Layout 的非就绪分支）。从 StudioCanvasIslands 拆出
 * （体积预算）。 */
import { IconButton, Tooltip } from '@mui/material'
import { useNavigate, useParams } from 'react-router-dom'
import { MaterialIcon } from '../../../components/MaterialIcon'
import styles from './StudioCanvasIslands.module.css'

export function StudioCanvasBackIsland() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  return (
    <div
      className={`${styles.island} ${styles.identity}`}
      style={{ top: 12 }}
      data-testid="studio-back-island"
    >
      <Tooltip title="返回">
        <IconButton
          size="small"
          aria-label="返回"
          data-testid="app-bar-back"
          onClick={() =>
            navigate(workspaceId ? `/workspaces/${workspaceId}` : '/')
          }
        >
          <MaterialIcon name="arrow_back" />
        </IconButton>
      </Tooltip>
    </div>
  )
}
