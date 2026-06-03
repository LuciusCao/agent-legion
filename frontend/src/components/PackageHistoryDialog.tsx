import { useEffect, useState } from 'react'
import { deletePackage, updatePackageName } from '../api'
import { triggerDownload } from '../lib/download'
import { usePackageStore } from '../stores/packageStore'
import styles from './PackageHistoryDialog.module.css'

interface PackageItem {
  id: number
  name: string
  path: string
  video_count: number
  size_bytes: number
  created_at: string
}

interface Props {
  open: boolean
  onClose: () => void
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

export function PackageHistoryDialog({ open, onClose }: Props) {
  const packages = usePackageStore((state) => state.packages)
  const loading = usePackageStore((state) => state.loading)
  const fetchPackagesList = usePackageStore((state) => state.fetchPackagesList)
  const removePackage = usePackageStore((state) => state.removePackage)
  const renamePackage = usePackageStore((state) => state.renamePackage)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

  useEffect(() => {
    if (open) {
      fetchPackagesList()
    }
  }, [open, fetchPackagesList])

  if (!open) return null

  const handleDownload = (pkg: PackageItem) => {
    const filename = pkg.path.split('/').pop() || ''
    if (filename) {
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
      await updatePackageName(id, editValue.trim())
      renamePackage(id, editValue.trim())
    } catch (err) {
      alert('重命名失败: ' + (err instanceof Error ? err.message : String(err)))
    }
    setEditingId(null)
  }

  return (
    <md-dialog open>
      <div slot="headline">包历史</div>
      <div slot="content" className={styles.dialogContent}>
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
                    <md-outlined-text-field
                      value={editValue}
                      onInput={(e: Event) =>
                        setEditValue((e.target as HTMLInputElement).value)
                      }
                      onKeyDown={(e: KeyboardEvent) => {
                        if (e.key === 'Enter') handleRename(pkg.id)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      style={{ width: '100%' }}
                    />
                  ) : (
                    <>
                      <span
                        className={styles.itemName}
                        onClick={() => startEdit(pkg)}
                        style={{ cursor: 'pointer' }}
                        title="点击重命名"
                      >
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
                  {editingId === pkg.id ? (
                    <md-icon-button onClick={() => handleRename(pkg.id)} title="确认">
                      <md-icon>check</md-icon>
                    </md-icon-button>
                  ) : (
                    <>
                      <md-icon-button onClick={() => handleDownload(pkg)} title="下载">
                        <md-icon>download</md-icon>
                      </md-icon-button>
                      <md-icon-button
                        onClick={() => handleDelete(pkg.id)}
                        title="删除"
                        style={{ color: 'var(--md-sys-color-error)' }}
                      >
                        <md-icon>delete</md-icon>
                      </md-icon-button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div slot="actions">
        <md-text-button onClick={onClose}>关闭</md-text-button>
      </div>
    </md-dialog>
  )
}
