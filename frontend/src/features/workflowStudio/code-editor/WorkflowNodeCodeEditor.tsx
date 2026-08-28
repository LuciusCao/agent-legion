import { useState } from 'react'
import { Button } from '@mui/material'
import styles from './WorkflowNodeCodeSection.module.css'

// Edit form for a custom node code draft: the code text plus an optional
// change note recorded on the version (audit trail).
export function WorkflowNodeCodeEditor(props: {
  initialCode: string
  busy: boolean
  onSave: (code: string, changeNote: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(props.initialCode)
  const [changeNote, setChangeNote] = useState('')
  return (
    <>
      <textarea
        aria-label="节点代码内容"
        className={styles.editor}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        disabled={props.busy}
      />
      <input
        aria-label="变更说明"
        className={styles.noteInput}
        placeholder="变更说明（可选）"
        value={changeNote}
        onChange={(event) => setChangeNote(event.target.value)}
        disabled={props.busy}
      />
      <div className={styles.actions}>
        <Button
          variant="outlined"
          size="small"
          onClick={() => props.onSave(draft, changeNote)}
          disabled={props.busy}
        >
          {props.busy ? '保存中...' : '保存草稿'}
        </Button>
        <Button
          variant="text"
          size="small"
          onClick={props.onCancel}
          disabled={props.busy}
        >
          取消
        </Button>
      </div>
      <div className={styles.hint}>
        草稿保存后不生效，点击「发布」后新执行才使用。
      </div>
    </>
  )
}
