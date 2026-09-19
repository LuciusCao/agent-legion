import { TextField } from '@mui/material'

import styles from './AddItemsDialog.module.css'

/** 与后端 run_text_items.TEXT_ITEM_MAX_BYTES 一致（UTF-8 字节数）。 */
export const TEXT_ITEM_MAX_BYTES = 64 * 1024
/** 与后端 run_text_items.DEFAULT_TEXT_FILENAME 一致。 */
export const DEFAULT_TEXT_FILENAME = '需求.md'

const encoder = new TextEncoder()

/** 文本条目的 UTF-8 字节数（后端按字节限长，中文一字三字节）。 */
export const textItemBytes = (text: string): number =>
  encoder.encode(text).length

/** 文本条目是否可提交：非空白且不超长。 */
export const textItemReady = (text: string): boolean =>
  text.trim().length > 0 && textItemBytes(text) <= TEXT_ITEM_MAX_BYTES

/** 提交给 runs API 的 text 条目；空文件名回落默认值。 */
export const textRunItem = (text: string, filename: string) => ({
  type: 'text' as const,
  content: text,
  filename: filename.trim() || DEFAULT_TEXT_FILENAME,
})

type AddItemsTextPanelProps = {
  text: string
  filename: string
  onTextChange: (value: string) => void
  onFilenameChange: (value: string) => void
}

/**
 * Text item type panel: requirement text typed straight into the dialog.
 * 一段文本 = 1 个条目 = 1 个 job；后端存成一份 Markdown 材料，后续节点看到的
 * 与手动上传同名文件完全一样。
 */
export function AddItemsTextPanel({
  text,
  filename,
  onTextChange,
  onFilenameChange,
}: AddItemsTextPanelProps) {
  const bytes = textItemBytes(text)
  const tooLong = bytes > TEXT_ITEM_MAX_BYTES
  return (
    <>
      <TextField
        label="文件名"
        value={filename}
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
        label="需求内容"
        placeholder="直接写下这次任务的需求，例如参考对象、目标、风格与交付要求"
        value={text}
        onChange={(event) => onTextChange(event.target.value)}
        error={tooLong}
        fullWidth
      />
      <div
        className={tooLong ? styles.errorHint : styles.summary}
        data-testid="text-summary"
      >
        {tooLong
          ? `内容过长：${bytes} 字节，上限 ${TEXT_ITEM_MAX_BYTES} 字节`
          : text.trim()
            ? `将作为 1 个条目提交（${bytes} 字节）`
            : '填写后作为 1 个条目提交'}
      </div>
    </>
  )
}
