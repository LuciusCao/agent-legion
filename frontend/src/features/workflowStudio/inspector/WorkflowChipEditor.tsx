import { Menu, MenuItem } from '@mui/material'
import { useState } from 'react'
import styles from './WorkflowChipEditor.module.css'

/**
 * M48 新设计的 chip 编辑器（用户确认原型）：chip + × 删除 + 虚线「添加」
 * 按钮。两种添加方式：传 addOptions 时下拉选择（tools 接 per-runtime
 * 工具目录）；不传则内联自由文本输入（requires_labels，Enter 提交 /
 * Esc 取消）。只读态整体弱化且不可交互。
 */
export function WorkflowChipEditor(props: {
  ariaLabel: string
  chips: string[]
  onRemove: (chip: string) => void
  onAdd: (value: string) => void
  addLabel: string
  addOptions?: string[]
  addInputPlaceholder?: string
  readOnly?: boolean
}) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const [inputOpen, setInputOpen] = useState(false)
  const [inputValue, setInputValue] = useState('')

  const commitInput = () => {
    const value = inputValue.trim()
    if (value) props.onAdd(value)
    setInputValue('')
    setInputOpen(false)
  }

  return (
    <div
      className={`${styles.editor} ${props.readOnly ? styles.editorDisabled : ''}`}
      aria-label={props.ariaLabel}
    >
      {props.chips.map((chip) => (
        <span key={chip} className={styles.chip}>
          {chip}
          <button
            type="button"
            className={styles.chipRemove}
            aria-label={`移除 ${chip}`}
            disabled={props.readOnly}
            onClick={() => props.onRemove(chip)}
          >
            ×
          </button>
        </span>
      ))}
      {props.chips.length === 0 && !inputOpen && (
        <span className={styles.addEmpty}>未配置</span>
      )}
      {inputOpen && !props.readOnly ? (
        <input
          // 内联添加输入：点开即聚焦，Enter 提交、Esc/失焦取消
          autoFocus
          className={styles.addInput}
          value={inputValue}
          placeholder={props.addInputPlaceholder}
          aria-label={props.addLabel}
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              commitInput()
            }
            if (event.key === 'Escape') {
              setInputValue('')
              setInputOpen(false)
            }
          }}
          onBlur={commitInput}
        />
      ) : (
        <button
          type="button"
          className={styles.addButton}
          disabled={props.readOnly}
          onClick={(event) => {
            if (props.addOptions) setMenuAnchor(event.currentTarget)
            else setInputOpen(true)
          }}
        >
          <span className={styles.addIcon}>+</span>
          {props.addLabel}
        </button>
      )}
      <Menu
        anchorEl={menuAnchor}
        open={menuAnchor !== null}
        onClose={() => setMenuAnchor(null)}
      >
        {(props.addOptions ?? []).length === 0 ? (
          <MenuItem disabled>目录内没有更多可添加项</MenuItem>
        ) : (
          (props.addOptions ?? []).map((option) => (
            <MenuItem
              key={option}
              onClick={() => {
                props.onAdd(option)
                setMenuAnchor(null)
              }}
            >
              {option}
            </MenuItem>
          ))
        )}
      </Menu>
    </div>
  )
}
