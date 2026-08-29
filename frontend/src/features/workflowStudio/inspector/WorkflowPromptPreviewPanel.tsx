import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { Button } from '@mui/material'
import styles from './WorkflowPromptPreviewPanel.module.css'

/** 详情 panel 内的运行 Prompt 预览（原位替换 inspector，不开 dialog）。 */
export function WorkflowPromptPreviewPanel(props: {
  nodeLabel: string
  prompt: string
  onBack: () => void
}) {
  return (
    <section aria-label="Prompt 预览" className={styles.panel}>
      <div className={styles.header}>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={props.onBack}
        >
          返回节点详情
        </Button>
        <span className={styles.title}>{props.nodeLabel} · 运行 Prompt</span>
      </div>
      <pre className={styles.prompt}>{props.prompt}</pre>
    </section>
  )
}
