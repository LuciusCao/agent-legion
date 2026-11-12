import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp'
import { useState } from 'react'
import styles from './WorkflowInspectorDisclosure.module.css'

export function WorkflowInspectorDisclosure(props: {
  title: string
  summary?: string
  children: React.ReactNode
  defaultExpanded?: boolean
  /** M48：标题尾部的通道徽标（结构通道 · 发布生效）。 */
  titleSuffix?: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(props.defaultExpanded ?? false)
  return (
    <section className={styles.disclosure}>
      <button
        className={styles.trigger}
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className={styles.title}>
          {props.title}
          {props.titleSuffix}
        </span>
        {props.summary && (
          <span className={styles.summary}>{props.summary}</span>
        )}
        {expanded ? (
          <KeyboardArrowUpIcon className={styles.icon} fontSize="small" />
        ) : (
          <ExpandMoreIcon className={styles.icon} fontSize="small" />
        )}
      </button>
      {expanded && <div className={styles.body}>{props.children}</div>}
    </section>
  )
}
