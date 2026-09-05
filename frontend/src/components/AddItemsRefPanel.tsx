import { useMemo } from 'react'
import { TextField } from '@mui/material'

import { parseRefIds } from '../lib/addItems'
import { ConnectionKeyField } from './settings/ConnectionKeyField'
import styles from './AddItemsDialog.module.css'

type AddItemsRefPanelProps = {
  connectionKey: string
  refText: string
  onConnectionKeyChange: (value: string) => void
  onRefTextChange: (value: string) => void
}

/**
 * Ref item type panel: external connection key plus one external ID per line.
 * 连接 Key 的选择/唯一默认/降级逻辑见 ConnectionKeyField（#419）。
 */
export function AddItemsRefPanel({
  connectionKey,
  refText,
  onConnectionKeyChange,
  onRefTextChange,
}: AddItemsRefPanelProps) {
  const refCount = useMemo(() => parseRefIds(refText).length, [refText])

  return (
    <>
      <ConnectionKeyField
        connectionKey={connectionKey}
        onConnectionKeyChange={onConnectionKeyChange}
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
