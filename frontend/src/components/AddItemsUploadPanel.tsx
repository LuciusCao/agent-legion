import { useMemo } from 'react'
import { Button } from '@mui/material'

import { formatBytes } from '../lib/addItems'
import styles from './AddItemsDialog.module.css'
import { STATUS_LABELS, type UploadEntry } from './useMaterialUploads'

type AddItemsUploadPanelProps = {
  entries: UploadEntry[]
  onAddFiles: (files: FileList | null) => void
  onRetry: (key: string) => void
  onRemove: (key: string) => void
}

/** Material item type panel: file/folder pickers plus the upload list. */
export function AddItemsUploadPanel({
  entries,
  onAddFiles,
  onRetry,
  onRemove,
}: AddItemsUploadPanelProps) {
  const totalSize = useMemo(
    () => entries.reduce((sum, entry) => sum + entry.size, 0),
    [entries]
  )
  const groupCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const entry of entries) {
      counts.set(entry.group, (counts.get(entry.group) ?? 0) + 1)
    }
    return Array.from(counts.entries())
  }, [entries])

  return (
    <>
      <div style={{ display: 'flex', gap: '8px' }}>
        <Button variant="outlined" component="label">
          选择文件
          <input
            type="file"
            multiple
            hidden
            data-testid="add-items-file-input"
            onChange={(event) => {
              onAddFiles(event.target.files)
              event.target.value = ''
            }}
          />
        </Button>
        <Button variant="outlined" component="label">
          选择文件夹
          <input
            type="file"
            multiple
            hidden
            data-testid="add-items-folder-input"
            {...{ webkitdirectory: '' }}
            onChange={(event) => {
              onAddFiles(event.target.files)
              event.target.value = ''
            }}
          />
        </Button>
      </div>
      {entries.length > 0 && (
        <>
          <div className={styles.summary} data-testid="upload-summary">
            {groupCounts
              .map(([group, count]) => `${group} × ${count}`)
              .join('，')}
            ，共 {formatBytes(totalSize)}
          </div>
          <div className={styles.fileList}>
            {entries.map((entry) => (
              <div className={styles.fileRow} key={entry.key}>
                <span className={styles.fileName} title={entry.name}>
                  {entry.name}
                </span>
                <span className={styles.fileSize}>
                  {formatBytes(entry.size)}
                </span>
                <span
                  className={
                    entry.status === 'failed'
                      ? styles.statusFailed
                      : entry.status === 'done'
                        ? styles.statusDone
                        : styles.statusPending
                  }
                >
                  {STATUS_LABELS[entry.status]}
                  {entry.deduplicated && entry.status === 'done'
                    ? '（已存在）'
                    : ''}
                </span>
                {entry.status === 'failed' && (
                  <Button size="small" onClick={() => onRetry(entry.key)}>
                    重试
                  </Button>
                )}
                {(entry.status === 'failed' || entry.status === 'pending') && (
                  <Button size="small" onClick={() => onRemove(entry.key)}>
                    移除
                  </Button>
                )}
              </div>
            ))}
            {entries.some((entry) => entry.status === 'failed') && (
              <div className={styles.errorHint}>
                失败文件不会包含在本次运行中，可重试或移除。
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
