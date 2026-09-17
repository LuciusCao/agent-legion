import { useState } from 'react'
import { Menu, MenuItem } from '@mui/material'
import type { ChipOption } from './studioChatConfigOptions'
import styles from './StudioChatComposer.module.css'

/** composer 工具行的紧凑配置触发器（#695 R4）：「文本 + ▾」chip 点开 MUI
 * Menu；header 项渲染为禁用的小字分组标题（对应原生 select 的 optgroup）。 */
export function StudioChatConfigChip(props: {
  label: string
  text: string
  title?: string
  disabled: boolean
  options: ChipOption[]
  onPick: (value: string) => void
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return (
    <>
      <button
        type="button"
        className={styles.chip}
        aria-label={props.label}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        title={props.title}
        disabled={props.disabled}
        onClick={(event) => setAnchor(event.currentTarget)}
      >
        <span className={styles.chipText}>{props.text}</span>▾
      </button>
      <Menu
        anchorEl={anchor}
        open={anchor !== null}
        onClose={() => setAnchor(null)}
      >
        {props.options.map((option) =>
          option.header ? (
            <MenuItem key={option.value} disabled className={styles.menuHeader}>
              {option.label}
            </MenuItem>
          ) : (
            <MenuItem
              key={option.value}
              selected={option.current}
              disabled={option.disabled}
              title={option.title}
              onClick={() => {
                props.onPick(option.value)
                setAnchor(null)
              }}
            >
              {option.label}
            </MenuItem>
          )
        )}
      </Menu>
    </>
  )
}
