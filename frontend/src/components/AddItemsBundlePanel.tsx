import { Button } from '@mui/material'

import { formatBytes } from '../lib/addItems'
import { BUNDLE_STATUS_LABELS, type BundleEntry } from '../lib/bundleFolder'
import styles from './AddItemsDialog.module.css'

type AddItemsBundlePanelProps = {
  bundles: BundleEntry[]
  onAddFolder: (files: FileList | null) => void
  onRetry: (key: string) => void
  onRemove: (key: string) => void
}

/** Bundle item type panel: folder picker plus one row per uploaded bundle. */
export function AddItemsBundlePanel({
  bundles,
  onAddFolder,
  onRetry,
  onRemove,
}: AddItemsBundlePanelProps) {
  return (
    <>
      <div style={{ display: 'flex', gap: '8px' }}>
        <Button variant="outlined" component="label">
          选择文件夹
          <input
            type="file"
            multiple
            hidden
            data-testid="add-items-bundle-input"
            {...{ webkitdirectory: '' }}
            onChange={(event) => {
              onAddFolder(event.target.files)
              event.target.value = ''
            }}
          />
        </Button>
      </div>
      <div className={styles.summary}>
        文件夹整体打包为一个条目运行，成员文件逐上传后生成 manifest。
      </div>
      {bundles.length > 0 && (
        <div className={styles.fileList} data-testid="bundle-list">
          {bundles.map((bundle) => {
            const totalSize = bundle.files.reduce(
              (sum, entry) => sum + entry.size,
              0
            )
            const doneCount = bundle.files.filter(
              (entry) => entry.status === 'done'
            ).length
            return (
              <div key={bundle.key}>
                <div className={styles.fileRow} data-testid="bundle-row">
                  <span className={styles.fileName} title={bundle.name}>
                    {bundle.name}
                  </span>
                  <span className={styles.fileSize}>
                    {bundle.files.length} 个文件，{formatBytes(totalSize)}
                  </span>
                  <span
                    className={
                      bundle.status === 'failed'
                        ? styles.statusFailed
                        : bundle.status === 'ready'
                          ? styles.statusDone
                          : styles.statusPending
                    }
                  >
                    {BUNDLE_STATUS_LABELS[bundle.status]}
                    {bundle.status === 'uploading'
                      ? ` ${doneCount}/${bundle.files.length}`
                      : ''}
                  </span>
                  {bundle.status === 'failed' && (
                    <Button size="small" onClick={() => onRetry(bundle.key)}>
                      重试
                    </Button>
                  )}
                  {(bundle.status === 'failed' ||
                    bundle.status === 'uploading') && (
                    <Button size="small" onClick={() => onRemove(bundle.key)}>
                      移除
                    </Button>
                  )}
                </div>
                {bundle.error && (
                  <div className={styles.errorHint}>{bundle.error}</div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
