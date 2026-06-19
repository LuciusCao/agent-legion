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
import { deletePackage, updatePackage, api } from '../api'
import { triggerDownload } from '../lib/download'
import { usePackageStore } from '../stores/packageStore'
import { MaterialIcon } from './MaterialIcon'
import styles from './PackageHistoryDialog.module.css'

interface PackageItem {
  id: number
  name: string
  path: string
  video_count: number
  size_bytes: number
  locked: number
  created_at: string
}

interface Props {
  open: boolean
  onClose: () => void
  scope?: 'global' | 'workspace'
  workspaceId?: string
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

function formatRelativeTime(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return '刚刚'
  if (diffMins < 60) return `${diffMins}分钟前`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}小时前`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 30) return `${diffDays}天前`
  return date.toLocaleDateString('zh-CN')
}

export function PackageHistoryDialog({
  open,
  onClose,
  scope = 'global',
  workspaceId,
}: Props) {
  const packages = usePackageStore((state) => state.packages)
  const loading = usePackageStore((state) => state.loading)
  const fetchPackagesList = usePackageStore((state) => state.fetchPackagesList)
  const removePackage = usePackageStore((state) => state.removePackage)
  const renamePackage = usePackageStore((state) => state.renamePackage)
  const toggleLockStore = usePackageStore((state) => state.toggleLock)

  const [workspacePackages, setWorkspacePackages] = useState<PackageItem[]>([])
  const [wsLoading, setWsLoading] = useState(false)

  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

  const isWorkspace = scope === 'workspace'
  const displayPackages = isWorkspace ? workspacePackages : packages
  const displayLoading = isWorkspace ? wsLoading : loading

  useEffect(() => {
    if (!open) return
    if (isWorkspace && workspaceId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWsLoading(true)
      api<{ packages: PackageItem[] }>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/packages`
      )
        .then((data) => {
          setWorkspacePackages(data.packages || [])
        })
        .catch(() => setWorkspacePackages([]))
        .finally(() => setWsLoading(false))
    } else if (!isWorkspace) {
      fetchPackagesList()
    }
  }, [open, isWorkspace, workspaceId, fetchPackagesList])

  const handleDownload = (pkg: PackageItem) => {
    const filename = pkg.path.split('/').pop() || ''
    if (!filename) return
    if (isWorkspace && workspaceId) {
      triggerDownload(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${filename}`
      )
    } else {
      triggerDownload(`/api/packages/${filename}`)
    }
  }

  const handleDelete = async (id: number) => {
    if (!window.confirm('确定要删除这个包吗？')) return
    try {
      await deletePackage(id)
      removePackage(id)
    } catch (err) {
      alert('删除失败: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  const handleToggleLock = async (pkg: PackageItem) => {
    const newLocked = !pkg.locked
    try {
      await updatePackage(pkg.id, { locked: newLocked })
      toggleLockStore(pkg.id, newLocked)
    } catch (err) {
      alert('操作失败: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  const startEdit = (pkg: PackageItem) => {
    setEditingId(pkg.id)
    setEditValue(pkg.name)
  }

  const handleRename = async (id: number) => {
    if (!editValue.trim()) {
      setEditingId(null)
      return
    }
    try {
      await updatePackage(id, { name: editValue.trim() })
      renamePackage(id, editValue.trim())
    } catch (err) {
      alert('重命名失败: ' + (err instanceof Error ? err.message : String(err)))
    }
    setEditingId(null)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>包历史</DialogTitle>
      <DialogContent className={styles.dialogContent}>
        {isWorkspace && !workspaceId && (
          <div className={styles.empty}>未提供工作区 ID</div>
        )}
        {displayLoading && <div className={styles.empty}>加载中...</div>}
        {!displayLoading && displayPackages.length === 0 && (
          <div className={styles.empty}>暂无打包记录</div>
        )}
        {!displayLoading && displayPackages.length > 0 && (
          <div className={styles.list}>
            {displayPackages.map((pkg) => (
              <div key={pkg.id} className={styles.item}>
                <div className={styles.itemInfo}>
                  {editingId === pkg.id && !isWorkspace ? (
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
                        onClick={() => !isWorkspace && startEdit(pkg)}
                        style={{
                          cursor: isWorkspace ? 'default' : 'pointer',
                        }}
                        title={isWorkspace ? '' : '点击重命名'}
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
                        {pkg.video_count}个视频 · {formatSize(pkg.size_bytes)} ·{' '}
                        {formatRelativeTime(pkg.created_at)}
                      </span>
                    </>
                  )}
                </div>
                <div className={styles.itemActions}>
                  {editingId === pkg.id && !isWorkspace ? (
                    <IconButton
                      onClick={() => handleRename(pkg.id)}
                      title="确认"
                      size="small"
                    >
                      <MaterialIcon name="check" />
                    </IconButton>
                  ) : (
                    <>
                      {!isWorkspace && (
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
                      {isWorkspace && (
                        <IconButton
                          onClick={() => handleDownload(pkg)}
                          title="下载"
                          size="small"
                        >
                          <MaterialIcon name="download" />
                        </IconButton>
                      )}
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
