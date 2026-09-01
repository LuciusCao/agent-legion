import { Button, Chip } from '@mui/material'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import styles from './WorkflowNodePromptEditor.module.css'

export type NodePromptEditorProps = {
  isDefault: boolean
  instructions: string
  skillKey: string | null
  previewError: string
  loading: boolean
  readOnly: boolean
  onPatch: (value: string) => void
}

/** 运行 Prompt 面板的编辑半区：绑定技能芯片（点击跳技能文件预览）+
 * 「节点指令」编辑区（留空 = 默认组装并标注；自定义整段替代；可重置为默认）。 */
export function WorkflowNodePromptEditor(props: NodePromptEditorProps) {
  const showPreview = useShowNodeDetailPreview()
  return (
    <>
      <div className={styles.toolbar}>
        <span className={styles.toolbarLabel}>绑定技能</span>
        {props.skillKey ? (
          <Chip
            size="small"
            label={props.skillKey}
            clickable
            onClick={() => showPreview('skill')}
          />
        ) : (
          <Chip size="small" label="未绑定技能" variant="outlined" disabled />
        )}
      </div>
      <div className={styles.editor}>
        <div className={styles.editorHeader}>
          <span className={styles.editorTitle}>节点指令</span>
          {props.isDefault && (
            <span className={styles.defaultBadge}>
              默认（按节点信息自动组装）
            </span>
          )}
          {!props.isDefault && !props.readOnly && (
            <Button size="small" onClick={() => props.onPatch('')}>
              重置为默认
            </Button>
          )}
        </div>
        <textarea
          aria-label="节点指令"
          className={styles.instructions}
          value={props.instructions}
          rows={10}
          disabled={props.readOnly}
          placeholder={props.loading ? '正在加载默认指令…' : ''}
          onChange={(event) => props.onPatch(event.target.value)}
        />
        <span className={styles.hint}>
          自定义内容将整段替代自动组装的默认指令；重置为默认后恢复自动组装。
        </span>
        {props.previewError && (
          <span className={styles.error} role="alert">
            预览加载失败：{props.previewError}
          </span>
        )}
      </div>
    </>
  )
}
