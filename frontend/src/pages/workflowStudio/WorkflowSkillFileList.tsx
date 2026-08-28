import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import { Button } from '@mui/material'
import type { SkillFile } from '../../types/agentCatalogTypes'
import styles from './WorkflowSkillPreviewPanel.module.css'

/** 技能预览左侧的文件列表（与 panel 共用样式模块）。 */
export function WorkflowSkillFileList(props: {
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
