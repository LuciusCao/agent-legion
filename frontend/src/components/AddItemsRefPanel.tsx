import { useMemo } from 'react'
import { TextField } from '@mui/material'

import { parseRefIds } from '../lib/addItems'
import styles from './AddItemsDialog.module.css'

type AddItemsRefPanelProps = {
  connectionKey: string
  refText: string
  onConnectionKeyChange: (value: string) => void
  onRefTextChange: (value: string) => void
}

/** Ref item type panel: external connection key plus one external ID per line. */
export function AddItemsRefPanel({
  connectionKey,
  refText,
  onConnectionKeyChange,
  onRefTextChange,
}: AddItemsRefPanelProps) {
  const refCount = useMemo(() => parseRefIds(refText).length, [refText])

  return (
    <>
      <TextField
        label="连接 Key"
        placeholder="workflow 绑定的外部服务连接 key"
        value={connectionKey}
        onChange={(event) => onConnectionKeyChange(event.target.value)}
        fullWidth
      />
      <TextField
        multiline
        rows={8}
        label="外部 ID"
        placeholder="一行一个 ID"
        value={refText}
        onChange={(event) => onRefTextChange(event.target.value)}
        fullWidth
      />
      {refCount > 0 && (
        <div className={styles.summary} data-testid="ref-summary">
          已解析 {refCount} 条引用
        </div>
      )}
    </>
  )
}
