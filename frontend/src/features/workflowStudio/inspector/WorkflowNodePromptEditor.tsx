import { Button, Chip, MenuItem, TextField } from '@mui/material'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import styles from './WorkflowNodePromptEditor.module.css'

export type NodePromptEditorProps = {
  isDefault: boolean
  instructions: string
  skillKey: string | null
  previewError: string
  loading: boolean
  readOnly: boolean
  /** #513：自定义提示词拼接模式（append/overwrite），由草稿 YAML 归一。 */
  promptMode: 'append' | 'overwrite'
  onPatch: (value: string) => void
  onPatchMode: (mode: 'append' | 'overwrite') => void
}

/** 运行 Prompt 面板的编辑半区：绑定技能芯片（点击跳技能文件预览）+
 * 「自定义提示词」编辑区（#513 定稿：默认为空追加；模式开关
 * 追加/覆写，追加=默认指令+自定义，覆写=仅自定义。平台提示词
 * 两种模式下都不可覆盖）。 */
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
          <span className={styles.editorTitle}>自定义提示词</span>
          {/* #513 复审：默认留空是常态，不挂徽标；仅自定义时提示（重置
              按钮同态出现）。 */}
          {!props.isDefault && (
            <span className={styles.defaultBadge}>已自定义</span>
          )}
          {!props.isDefault && !props.readOnly && (
            <Button size="small" onClick={() => props.onPatch('')}>
              清空
            </Button>
          )}
        </div>
        {/* #513 定稿：说明只讲行为本身 + 模式开关说明。 */}
        <span className={styles.hint}>
          自定义提示词默认为空，如果填写，将按下方模式拼入平台提示词，构成运行时使用的提示词。
        </span>
        <TextField
          select
          label="模式"
          variant="outlined"
          size="small"
          className={styles.modeSelect}
          value={props.promptMode}
          disabled={props.readOnly}
          onChange={(e) =>
            props.onPatchMode(e.target.value as 'append' | 'overwrite')
          }
        >
          <MenuItem value="append">追加（平台提示词 + 自定义提示词）</MenuItem>
          <MenuItem value="overwrite">覆写（仅自定义提示词）</MenuItem>
        </TextField>
        <textarea
          aria-label="自定义提示词"
          className={styles.instructions}
          value={props.instructions}
          rows={10}
          disabled={props.readOnly}
          placeholder={props.loading ? '正在加载默认指令…' : ''}
          onChange={(event) => props.onPatch(event.target.value)}
        />
        {props.previewError && (
          <span className={styles.error} role="alert">
            预览加载失败：{props.previewError}
          </span>
        )}
      </div>
    </>
  )
}
