import { Chip } from '@mui/material'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import styles from './WorkflowNodePromptEditor.module.css'

export function WorkflowNodeSkillBinding({
  skillKey,
}: {
  skillKey: string | null
}) {
  const showPreview = useShowNodeDetailPreview()
  return (
    <div className={styles.toolbar}>
      <span className={styles.toolbarLabel}>绑定技能</span>
      {skillKey ? (
        <Chip
          size="small"
          label={skillKey}
          clickable
          onClick={() => showPreview('skill')}
        />
      ) : (
        <Chip size="small" label="未绑定技能" variant="outlined" disabled />
      )}
    </div>
  )
}
