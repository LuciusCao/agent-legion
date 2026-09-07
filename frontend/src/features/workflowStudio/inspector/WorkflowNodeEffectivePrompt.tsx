import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { useState } from 'react'
import styles from './WorkflowNodePromptEditor.module.css'

/** 平台提示词（#513 前叫「平台信封」）只读预览：可折叠，默认展开，
 * 置于面板顶部——先看不可修改的平台生成部分，再编辑自定义追加。 */
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
        平台提示词
      </button>
      {fullOpen && (
        <>
          {/* #513 复审：说明文案与 pre 正文同缩进（padding 对齐 .prompt
              的 0 20px），不贴面板左缘。 */}
          <span className={styles.effectiveHint}>
            根据 workflow 自动生成，不可修改
          </span>
          <pre className={styles.prompt}>
            {props.effectivePrompt ?? '正在加载…'}
          </pre>
        </>
      )}
    </div>
  )
}
