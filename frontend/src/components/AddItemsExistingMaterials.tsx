import { useMemo } from 'react'
import { Checkbox } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { formatBytes } from '../lib/addItems'
import type { MaterialListResponse } from '../types'
import styles from './AddItemsDialog.module.css'

type AddItemsExistingMaterialsProps = {
  workspaceId?: string
  enabled: boolean
  selectedIds: string[]
  onToggle: (materialId: string) => void
}

// 「已有材料」tab：列出 workspace 里已就绪的材料（含 demo 预置种子），
// 勾选的 id 由父组件并入创建运行的 payload（#154）。
export function AddItemsExistingMaterials({
  workspaceId,
  enabled,
  selectedIds,
  onToggle,
}: AddItemsExistingMaterialsProps) {
  const materialsQuery = useQuery({
    queryKey: extraQueryKeys.workspaceMaterials(workspaceId ?? ''),
    queryFn: () =>
      api<MaterialListResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId ?? '')}/materials`
      ),
    enabled,
  })
  const readyMaterials = useMemo(
    () =>
      (materialsQuery.data?.materials ?? []).filter(
        (material) => material.status === 'ready'
      ),
    [materialsQuery.data]
  )

  if (materialsQuery.isLoading) {
    return <div className={styles.summary}>材料列表加载中…</div>
  }
  if (materialsQuery.isError) {
    return (
      <div className={styles.errorHint}>
        材料列表加载失败，请切换 tab 后重试。
      </div>
    )
  }
  if (readyMaterials.length === 0) {
    return (
      <div className={styles.summary} data-testid="empty-materials">
        暂无可用材料，可先在「上传材料」tab 上传。
      </div>
    )
  }
  return (
    <div className={styles.fileList} data-testid="existing-materials-list">
      {readyMaterials.map((material) => (
        <div className={styles.fileRow} key={material.id}>
          <Checkbox
            size="small"
            checked={selectedIds.includes(material.id)}
            onChange={() => onToggle(material.id)}
            inputProps={{ 'aria-label': material.filename }}
          />
          <span className={styles.fileName} title={material.filename}>
            {material.filename}
          </span>
          <span className={styles.fileSize}>
            {formatBytes(material.size_bytes)}
          </span>
        </div>
      ))}
    </div>
  )
}
