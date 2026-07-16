import CloseIcon from '@mui/icons-material/Close'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import {
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
} from '@mui/material'
import { useEffect, useState } from 'react'
import { getSkillDetail } from '../../executorApi'
import type { SkillDetail, SkillFile } from '../../executorTypes'
import styles from './WorkflowSkillPreviewDialog.module.css'

export function WorkflowSkillPreviewDialog(props: {
  open: boolean
  skillKey: string
  onClose: () => void
}) {
  const [result, setResult] = useState<{
    skillKey: string
    detail: SkillDetail | null
    error: string
  } | null>(null)
  const [selectedPath, setSelectedPath] = useState('SKILL.md')
  useEffect(() => {
    if (!props.open || !props.skillKey) return
    let active = true
    void getSkillDetail(props.skillKey)
      .then((detail) => {
        if (active) setResult({ skillKey: props.skillKey, detail, error: '' })
      })
      .catch((reason) => {
        if (active)
          setResult({
            skillKey: props.skillKey,
            detail: null,
            error: reason instanceof Error ? reason.message : '加载失败',
          })
      })
    return () => {
      active = false
    }
  }, [props.open, props.skillKey])
  const current = result?.skillKey === props.skillKey ? result : null
  const detail = current?.detail ?? null
  const error = current?.error ?? ''
  const files = detail?.files ?? []
  const selected = files.find((file) => file.path === selectedPath) ?? files[0]

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="lg">
      <DialogTitle>
        {props.skillKey}
        <IconButton aria-label="关闭技能预览" onClick={props.onClose}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent className={styles.content} dividers>
        <SkillFileList
          files={files}
          selected={selected}
          onSelect={setSelectedPath}
        />
        <div className={styles.preview}>
          {error && <div className={styles.state}>{error}</div>}
          {!error && !detail && (
            <div className={styles.state}>正在加载技能文件...</div>
          )}
          {detail && files.length === 0 && (
            <div className={styles.state}>本地技能目录不可用</div>
          )}
          {selected && <pre className={styles.code}>{selected.content}</pre>}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function SkillFileList(props: {
  files: SkillFile[]
  selected: SkillFile | undefined
  onSelect: (path: string) => void
}) {
  return (
    <nav className={styles.fileList} aria-label="技能文件">
      {props.files.map((file) => (
        <Button
          className={styles.fileButton}
          color="inherit"
          key={file.path}
          startIcon={<DescriptionOutlinedIcon />}
          variant={props.selected?.path === file.path ? 'outlined' : 'text'}
          onClick={() => props.onSelect(file.path)}
        >
          <span>{file.path}</span>
        </Button>
      ))}
    </nav>
  )
}
