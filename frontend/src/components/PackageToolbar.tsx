import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import styles from './BatchToolbar.module.css'

export function PackageToolbar() {
  const {
    selectedIds,
    togglePackageSelectMode,
    selectPackageAll,
    selectPackageApproved,
    selectPackageUnpacked,
    clearSelection,
    batchPackage,
  } = useVideoStore()
  const { showToast } = useUiStore()

  const count = selectedIds.size
  const hasSelection = count > 0

  const handlePackage = async () => {
    if (!hasSelection) return
    await batchPackage(Array.from(selectedIds))
    togglePackageSelectMode()
    showToast('打包已提交，完成后将自动下载', 'success')
  }

  return (
    <div className={`${styles.batchToolbar} card-elevated`}>
      <span>已选择 {count} 项</span>
      <div className={styles.batchActions}>
        <md-text-button onClick={selectPackageAll}>全选</md-text-button>
        <md-text-button onClick={selectPackageApproved}>
          仅选审核通过
        </md-text-button>
        <md-text-button onClick={selectPackageUnpacked}>
          仅选未打包
        </md-text-button>
        <md-text-button onClick={clearSelection}>取消选择</md-text-button>
        <md-icon-button
          disabled={!hasSelection || undefined}
          onClick={handlePackage}
          title="打包"
        >
          <md-icon>inventory_2</md-icon>
        </md-icon-button>
        <md-outlined-button onClick={togglePackageSelectMode}>
          退出
        </md-outlined-button>
      </div>
    </div>
  )
}
