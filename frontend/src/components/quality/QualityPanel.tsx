import { useState } from 'react'
import { FormControl, InputLabel, MenuItem, Select } from '@mui/material'
import { useQualityBatches } from '../../hooks/useQuality'
import { QualityBatchesTab } from './QualityBatchesTab'
import { QualityLabelingTab } from './QualityLabelingTab'
import { QualityStatsTab } from './QualityStatsTab'
import styles from './QualityPanel.module.css'

type QualityTab = 'batches' | 'labeling' | 'stats'

const TABS: { key: QualityTab; label: string }[] = [
  { key: 'batches', label: '批次' },
  { key: 'labeling', label: '打标' },
  { key: 'stats', label: '统计' },
]

export interface QualityPanelProps {
  workspaceId: string
}

/**
 * 质量闭环面板：批次列表 → 抽样打标 → 聚合统计。
 * tab 与选中批次为本地状态（仓库页面无 URL search param 惯例，保持简单）。
 */
export function QualityPanel({ workspaceId }: QualityPanelProps) {
  const [tab, setTab] = useState<QualityTab>('batches')
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const batchesQuery = useQualityBatches(workspaceId)
  const batches = batchesQuery.data?.batches ?? []

  const selectBatch = (batchId: string) => {
    setSelectedBatchId(batchId)
    setTab('labeling')
  }

  return (
    <section className={styles.panel} aria-label="质量闭环">
      <div className={styles.controls}>
        <div className={styles.tabs} role="tablist" aria-label="质量闭环视图">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === tab}
              className={t.key === tab ? styles.activeTab : styles.tab}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab !== 'batches' && (
          <FormControl size="small" className={styles.batchSelector}>
            <InputLabel id="quality-batch-selector-label">批次</InputLabel>
            <Select
              labelId="quality-batch-selector-label"
              value={
                batches.some((b) => b.id === selectedBatchId)
                  ? (selectedBatchId ?? '')
                  : ''
              }
              label="批次"
              onChange={(e) => setSelectedBatchId(e.target.value || null)}
            >
              {batches.map((batch) => (
                <MenuItem key={batch.id} value={batch.id}>
                  {batch.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
      </div>

      {tab === 'batches' && (
        <QualityBatchesTab
          workspaceId={workspaceId}
          selectedBatchId={selectedBatchId}
          onSelectBatch={selectBatch}
        />
      )}
      {tab === 'labeling' &&
        (selectedBatchId ? (
          <QualityLabelingTab
            workspaceId={workspaceId}
            batchId={selectedBatchId}
          />
        ) : (
          <p className={styles.muted}>请先在「批次」页选择或新建一个抽样批次</p>
        ))}
      {tab === 'stats' &&
        (selectedBatchId ? (
          <QualityStatsTab
            workspaceId={workspaceId}
            batchId={selectedBatchId}
          />
        ) : (
          <p className={styles.muted}>请先在「批次」页选择或新建一个抽样批次</p>
        ))}
    </section>
  )
}
