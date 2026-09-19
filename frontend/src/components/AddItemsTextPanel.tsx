import { Button, TextField } from '@mui/material'

import {
  DEFAULT_TEXT_FILENAME,
  TEXT_ITEM_MAX_BYTES,
  type ResolvedTextItem,
  type StartTextInput,
} from '../lib/textItem'
import styles from './AddItemsDialog.module.css'

type AddItemsTextPanelProps = {
  item: ResolvedTextItem
  config: StartTextInput
  onTextChange: (value: string | null) => void
  onFilenameChange: (value: string) => void
}

function summaryText(item: ResolvedTextItem): string {
  if (item.tooLong)
    return `内容过长：${item.bytes} 字节，上限 ${TEXT_ITEM_MAX_BYTES} 字节`
  if (item.untouchedTemplate) return '请先按你的方向修改模板，再创建运行'
  if (item.content.trim()) return `将作为 1 个条目提交（${item.bytes} 字节）`
  return '填写后作为 1 个条目提交'
}

/**
 * Text item type panel: requirement text typed straight into the dialog.
 * 一段文本 = 1 个条目 = 1 个 job；后端存成一份 Markdown 材料，后续节点看到的
 * 与手动上传同名文件完全一样。模板与文件名默认值来自 Studio 入口节点的
 * text_input 配置。
 */
export function AddItemsTextPanel({
  item,
  config,
  onTextChange,
  onFilenameChange,
}: AddItemsTextPanelProps) {
  const warn = item.tooLong || item.untouchedTemplate
  return (
    <>
      <TextField
        label="文件名"
        value={item.filename}
        onChange={(event) => onFilenameChange(event.target.value)}
        placeholder={DEFAULT_TEXT_FILENAME}
        helperText="存成材料时使用的文件名，.md 或 .txt"
        size="small"
        fullWidth
      />
      <TextField
        multiline
        minRows={10}
        maxRows={20}
        label={config.label || '需求内容'}
        placeholder="直接写下这次任务的需求，例如参考对象、目标、风格与交付要求"
        value={item.content}
        onChange={(event) => onTextChange(event.target.value)}
        error={item.tooLong}
        fullWidth
      />
      <div
        style={{ display: 'flex', alignItems: 'center', gap: '12px' }}
        className={warn ? styles.errorHint : styles.summary}
        data-testid="text-summary"
      >
        <span style={{ flex: 1 }}>{summaryText(item)}</span>
        {config.template && item.content !== config.template && (
          <Button
            size="small"
            variant="text"
            onClick={() => onTextChange(null)}
          >
            恢复模板
          </Button>
        )}
      </div>
    </>
  )
}
