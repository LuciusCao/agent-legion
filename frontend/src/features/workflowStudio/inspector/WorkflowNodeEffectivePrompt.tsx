import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { useState } from 'react'
import styles from './WorkflowNodePromptEditor.module.css'

/** 完整运行 Prompt（含平台信封）只读预览：可折叠，默认展开。 */
export function WorkflowNodeEffectivePrompt(props: {
  effectivePrompt: string | null
}) {
  const [fullOpen, setFullOpen] = useState(true)
  return (
    <div className={styles.effective}>
      <button
        type="button"
        className={styles.effectiveToggle}
        aria-expanded={fullOpen}
        onClick={() => setFullOpen((open) => !open)}
      >
        <ExpandMoreIcon
          fontSize="small"
          style={{
            transform: fullOpen ? 'none' : 'rotate(-90deg)',
            transition: 'transform 0.15s',
          }}
        />
        完整运行 Prompt（含平台信封）
      </button>
      {fullOpen && (
        <pre className={styles.prompt}>
          {props.effectivePrompt ?? '正在加载…'}
        </pre>
      )}
    </div>
  )
}
