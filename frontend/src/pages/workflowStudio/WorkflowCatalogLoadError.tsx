import { Button } from '@mui/material'
import inspectorStyles from './WorkflowNodeInspector.module.css'

// executor 目录加载失败时的就地提示：明确区分「加载失败」与「未匹配到
// executor capability」，避免目录请求失败被误读成未配置绑定。
export function WorkflowCatalogLoadError(props: { onRetry: () => void }) {
  return (
    <div className={inspectorStyles.empty} role="alert">
      executor 目录加载失败，绑定信息不可用。
      <Button size="small" onClick={props.onRetry}>
        重试
      </Button>
    </div>
  )
}
