import { useState } from 'react'
import { Button, Chip } from '@mui/material'
import { toErrorMessage } from '../../lib/queryError'
import { useQualityBatches } from '../../hooks/useQuality'
import { CreateSampleBatchDialog } from './CreateSampleBatchDialog'
import type { QualitySampleBatchCreateResponse } from '../../api/qualityApi'
import styles from './QualityPanel.module.css'

export interface QualityBatchesTabProps {
  workspaceId: string
  selectedBatchId: string | null
  onSelectBatch: (batchId: string) => void
}

function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('zh-CN')
}

/** Tab 1：批次列表 + 新建抽样入口。 */
export function QualityBatchesTab({
  workspaceId,
  selectedBatchId,
  onSelectBatch,
}: QualityBatchesTabProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const query = useQualityBatches(workspaceId)
  const batches = query.data?.batches ?? []
  const error = toErrorMessage(query.error)

  const handleCreated = (batch: QualitySampleBatchCreateResponse) => {
    setDialogOpen(false)
    onSelectBatch(batch.id)
  }

  if (error) {
    return <p className={styles.error}>批次列表加载失败：{error}</p>
  }

  return (
    <div>
      <div className={styles.toolbar}>
        <Button
          variant="contained"
          size="small"
          onClick={() => setDialogOpen(true)}
        >
          新建抽样
        </Button>
      </div>
      <div className={styles.tableWrap}>
        <table aria-label="抽样批次列表">
          <thead>
            <tr>
              <th>名称</th>
              <th>创建时间</th>
              <th>样本量</th>
              <th>Seed</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch.id}>
                <td>
                  {batch.name}
                  {batch.id === selectedBatchId && (
                    <Chip
                      label="当前"
                      size="small"
                      color="primary"
                      variant="outlined"
                      sx={{ ml: 1 }}
                    />
                  )}
                </td>
                <td>{formatDateTime(batch.created_at)}</td>
                <td>{batch.sample_size}</td>
                <td>
                  <code>{batch.seed}</code>
                </td>
                <td>
                  <Button size="small" onClick={() => onSelectBatch(batch.id)}>
                    去打标
                  </Button>
                </td>
              </tr>
            ))}
            {query.isLoading && (
              <tr>
                <td colSpan={5} className={styles.emptyCell}>
                  加载中…
                </td>
              </tr>
            )}
            {!query.isLoading && batches.length === 0 && (
              <tr>
                <td colSpan={5} className={styles.emptyCell}>
                  暂无抽样批次，点击「新建抽样」开始
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <CreateSampleBatchDialog
        open={dialogOpen}
        workspaceId={workspaceId}
        onClose={() => setDialogOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  )
}
