import { useState } from 'react'
import { Chip, FormControlLabel, Checkbox } from '@mui/material'
import { toErrorMessage } from '../../lib/queryError'
import { useQualityBatchDetail } from '../../hooks/useQuality'
import type { QualitySampleItem } from '../../api/qualityApi'
import { QualityItemDetailPanel } from './QualityItemDetailPanel'
import styles from './QualityPanel.module.css'

export interface QualityLabelingTabProps {
  workspaceId: string
  batchId: string
}

function statusColor(status: string): 'success' | 'error' | 'default' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'error'
  return 'default'
}

function ItemRow({
  item,
  active,
  onSelect,
}: {
  item: QualitySampleItem
  active: boolean
  onSelect: () => void
}) {
  return (
    <li>
      <button
        type="button"
        className={active ? styles.itemRowActive : styles.itemRow}
        onClick={onSelect}
        aria-current={active || undefined}
      >
        <span className={styles.itemRowHeader}>
          <strong>{item.node_key}</strong>
          <Chip
            label={item.run_status}
            size="small"
            color={statusColor(item.run_status)}
            variant="outlined"
          />
          {item.current_label ? (
            <Chip
              label={item.current_label.verdict}
              size="small"
              color={
                item.current_label.verdict === 'good' ? 'success' : 'error'
              }
            />
          ) : (
            <Chip label="未打标" size="small" variant="outlined" />
          )}
        </span>
        <span className={styles.itemMeta}>
          {item.failure_category && <span>{item.failure_category} · </span>}
          {item.skill_version} · {item.model}
        </span>
      </button>
    </li>
  )
}

/** Tab 2：左侧样本列表 + 右侧打标详情。 */
export function QualityLabelingTab({
  workspaceId,
  batchId,
}: QualityLabelingTabProps) {
  const [unlabeledOnly, setUnlabeledOnly] = useState(false)
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null)
  const query = useQualityBatchDetail(workspaceId, batchId)
  const error = toErrorMessage(query.error)

  if (error) {
    return <p className={styles.error}>批次详情加载失败：{error}</p>
  }

  const items = (query.data?.items ?? []).filter(
    (item) => !unlabeledOnly || !item.current_label
  )
  // 未显式选择时默认选中第一个可见 item；切换过滤后选中项不可见时回退到第一个。
  const effectiveItemId =
    selectedItemId && items.some((item) => item.id === selectedItemId)
      ? selectedItemId
      : (items[0]?.id ?? null)

  return (
    <div className={styles.labelingLayout}>
      <aside className={styles.itemList} aria-label="样本列表">
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={unlabeledOnly}
              onChange={(e) => setUnlabeledOnly(e.target.checked)}
            />
          }
          label="仅未打标"
        />
        {query.isLoading && <p className={styles.muted}>加载中…</p>}
        {!query.isLoading && items.length === 0 && (
          <p className={styles.muted}>
            {unlabeledOnly ? '没有未打标的样本' : '该批次暂无样本'}
          </p>
        )}
        <ul className={styles.itemListUl}>
          {items.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              active={item.id === effectiveItemId}
              onSelect={() => setSelectedItemId(item.id)}
            />
          ))}
        </ul>
      </aside>
      <div className={styles.itemDetail}>
        {effectiveItemId ? (
          <QualityItemDetailPanel
            key={effectiveItemId}
            workspaceId={workspaceId}
            itemId={effectiveItemId}
          />
        ) : (
          !query.isLoading && <p className={styles.muted}>请选择样本</p>
        )}
      </div>
    </div>
  )
}
