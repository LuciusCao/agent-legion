import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import { Button } from '@mui/material'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import styles from './WorkflowAgentExecutionDetails.module.css'

/**
 * Agent 配置区（结构通道）：runtime 选择 / skill 绑定在
 * WorkflowNodeProfileBody，本组件承载预览入口。M48 双通道后 provider /
 * model / thinking / prompt / tools / requires_labels 已迁入「运行配置」
 * 专区（WorkflowNodeRuntimeChannelSection，运行通道即改即存），此处不再
 * 重复提供。
 */
export function WorkflowAgentExecutionDetails() {
  // 预览在详情 panel 内原位展开（不开 dialog），右侧 Agent 对话保持可见。
  const showPreview = useShowNodeDetailPreview()
  return (
    <div className={styles.actions}>
      <Button
        size="small"
        startIcon={<ArticleOutlinedIcon />}
        onClick={() => showPreview('prompt')}
      >
        查看 Prompt
      </Button>
      <Button
        size="small"
        startIcon={<FolderOpenOutlinedIcon />}
        onClick={() => showPreview('skill')}
      >
        浏览技能文件
      </Button>
    </div>
  )
}
