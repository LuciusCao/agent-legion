import { useEffect, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
} from '@mui/material'
import {
  deleteWorkspacePackage,
  fetchWorkspacePackages,
  updateWorkspacePackage,
} from '../api'
import { triggerDownload } from '../lib/download'
import { formatBytes, formatRelativeTime } from '../lib/formatters'
import { useUiStore } from '../stores/uiStore'
import type { WorkspacePackageItem } from '../types/packageTypes'
import { MaterialIcon } from './MaterialIcon'
import styles from './PackageHistoryDialog.module.css'

interface Props {
  open: boolean
  onClose: () => void
  workspaceId: string
}

export function PackageHistoryDialog({ open, onClose, workspaceId }: Props) {
  const { showToast } = useUiStore()
  const showError = (prefix: string, err: unknown) =>
    showToast(
      prefix + (err instanceof Error ? err.message : String(err)),
      'error'
    )
  const [packages, setPackages] = useState<WorkspacePackageItem[]>([])
  const [loading, setLoading] = useState(false)

  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

  useEffect(() => {
    if (!open) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    fetchWorkspacePackages(workspaceId)
      .then((data) => {
        setPackages(data.packages || [])
      })
      .catch(() => setPackages([]))
      .finally(() => setLoading(false))
  }, [open, workspaceId])

  const handleDownload = (pkg: WorkspacePackageItem) => {
    const filename = pkg.path.split('/').pop() || ''
    if (!filename) return
    triggerDownload(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${filename}`
    )
  }

  const handleDelete = async (id: number) => {
    if (!window.confirm('确定要删除这个包吗？')) return
    try {
      await deleteWorkspacePackage(workspaceId, id)
      setPackages((prev) => prev.filter((p) => p.id !== id))
    } catch (err) {
      showError('删除失败: ', err)
    }
  }

  const handleToggleLock = async (pkg: WorkspacePackageItem) => {
    const newLocked = !pkg.locked
    try {
      await updateWorkspacePackage(workspaceId, pkg.id, { locked: newLocked })
      setPackages((prev) =>
        prev.map((p) =>
          p.id === pkg.id ? { ...p, locked: newLocked ? 1 : 0 } : p
        )
      )
    } catch (err) {
      showError('操作失败: ', err)
    }
  }

  const startEdit = (pkg: WorkspacePackageItem) => {
    setEditingId(pkg.id)
    setEditValue(pkg.name)
  }

  const handleRename = async (id: number) => {
    if (!editValue.trim()) {
      setEditingId(null)
      return
    }
    try {
      await updateWorkspacePackage(workspaceId, id, {
        name: editValue.trim(),
      })
      setPackages((prev) =>
        prev.map((p) => (p.id === id ? { ...p, name: editValue.trim() } : p))
      )
    } catch (err) {
      showError('重命名失败: ', err)
    }
    setEditingId(null)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>包历史</DialogTitle>
      <DialogContent className={styles.dialogContent}>
        {loading && <div className={styles.empty}>加载中...</div>}
        {!loading && packages.length === 0 && (
          <div className={styles.empty}>暂无打包记录</div>
        )}
        {!loading && packages.length > 0 && (
          <div className={styles.list}>
            {packages.map((pkg) => (
              <div key={pkg.id} className={styles.item}>
                <div className={styles.itemInfo}>
                  {editingId === pkg.id ? (
                    <TextField
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRename(pkg.id)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      fullWidth
                      variant="outlined"
                      size="small"
                      autoFocus
                    />
                  ) : (
                    <>
                      <span
                        className={styles.itemName}
                        onClick={() => startEdit(pkg)}
                        style={{ cursor: 'pointer' }}
                        title="点击重命名"
                      >
                        {pkg.locked ? (
                          <MaterialIcon
                            name="lock"
                            sx={{
                              fontSize: '14px',
                              verticalAlign: 'middle',
                              marginRight: '4px',
                            }}
                          />
                        ) : null}
                        {pkg.name || '未命名'}
                      </span>
                      <span className={styles.itemMeta}>
                        {`${pkg.video_count}个任务`} ·{' '}
                        {formatBytes(pkg.size_bytes)} ·{' '}
                        {formatRelativeTime(pkg.created_at)}
                      </span>
                    </>
                  )}
                </div>
                <div className={styles.itemActions}>
                  {editingId === pkg.id ? (
                    <IconButton
                      onClick={() => handleRename(pkg.id)}
                      title="确认"
                      size="small"
                    >
                      <MaterialIcon name="check" />
                    </IconButton>
                  ) : (
                    <>
                      <IconButton
                        onClick={() => handleToggleLock(pkg)}
                        title={pkg.locked ? '解锁' : '锁定'}
                        size="small"
                      >
                        <MaterialIcon
                          name={pkg.locked ? 'lock' : 'lock_open'}
                        />
                      </IconButton>
                      <IconButton
                        onClick={() => handleDownload(pkg)}
                        title="下载"
                        size="small"
                      >
                        <MaterialIcon name="download" />
                      </IconButton>
                      <IconButton
                        disabled={!!pkg.locked}
                        onClick={() => handleDelete(pkg.id)}
                        title={pkg.locked ? '已锁定，无法删除' : '删除'}
                        size="small"
                        sx={{
                          color: pkg.locked ? '#9ca3af' : '#ba1a1a',
                        }}
                      >
                        <MaterialIcon name="delete" />
                      </IconButton>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} variant="text">
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
