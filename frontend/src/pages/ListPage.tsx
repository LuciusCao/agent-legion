import { useEffect, useRef, useCallback, useState } from 'react'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import { useVideoEvents } from '../hooks/useVideoEvents'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { StatCards } from '../components/StatCards'
import { VideoList } from '../components/VideoList'
import {
  BatchToolbar,
  type BatchFilter,
  type BatchAction,
} from '../components/BatchToolbar'
import { BatchRerunDialog } from '../components/BatchRerunDialog'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { RunToDialog } from '../components/RunToDialog'
import { AddDialog } from '../components/AddDialog'
import { PackageHistoryDialog } from '../components/PackageHistoryDialog'

export function ListPage() {
  const selectedType = useVideoStore((state) => state.selectedType)
  const searchQuery = useVideoStore((state) => state.searchQuery)
  const setSearchQuery = useVideoStore((state) => state.setSearchQuery)
  const setSelectedType = useVideoStore((state) => state.setSelectedType)
  const selectMode = useVideoStore((state) => state.selectMode)
  const fetchVideos = useVideoStore((state) => state.fetchVideos)
  const sseConnected = useVideoStore((state) => state.sseConnected)
  const toggleSelectMode = useVideoStore((state) => state.toggleSelectMode)
  const videos = useVideoStore((state) => state.videos)
  const selectedIds = useVideoStore((state) => state.selectedIds)
  const clearSelection = useVideoStore((state) => state.clearSelection)
  const selectAllVisible = useVideoStore((state) => state.selectAllVisible)
  const selectUnpacked = useVideoStore((state) => state.selectUnpacked)
  const selectReviewApproved = useVideoStore(
    (state) => state.selectReviewApproved
  )
  const selectReviewNotPassed = useVideoStore(
    (state) => state.selectReviewNotPassed
  )
  const batchDelete = useVideoStore((state) => state.batchDelete)
  const batchRunTo = useVideoStore((state) => state.batchRunTo)
  const batchPackage = useVideoStore((state) => state.batchPackage)
  const exitSelectMode = useVideoStore((state) => state.exitSelectMode)
  const { openAddDialog, showToast } = useUiStore()
  const [packageDialogOpen, setPackageDialogOpen] = useState(false)
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [runToDialogOpen, setRunToDialogOpen] = useState(false)

  const debouncedSetSearchQuery = useDebouncedCallback(setSearchQuery, 250)

  const handleFetchVideos = useCallback(async () => {
    await fetchVideos()
    const err = useVideoStore.getState().error
    if (err) {
      showToast(`加载失败: ${err}`, 'error')
      useVideoStore.getState().clearError()
    }
  }, [fetchVideos, showToast])
  const tabsRef = useRef<(HTMLElement & { activeTabIndex: number }) | null>(
    null
  )

  useEffect(() => {
    handleFetchVideos()
  }, [handleFetchVideos])

  useVideoEvents()

  useEffect(() => {
    const tabs = tabsRef.current
    if (!tabs) return
    const handleChange = () => {
      setSelectedType(tabs.activeTabIndex === 0 ? 'knowledge' : 'question')
    }
    tabs.addEventListener('change', handleChange)
    return () => tabs.removeEventListener('change', handleChange)
  }, [setSelectedType])

  const selectedVideos = videos.filter((video) => selectedIds.has(video.id))
  const count = selectedIds.size
  const hasSelection = count > 0

  const handleDeleteConfirm = async () => {
    const result = await batchDelete(Array.from(selectedIds))
    const succeeded = result.results.filter(
      (r) => r.status === 'deleted'
    ).length
    const failed = result.results.length - succeeded
    showToast(
      failed > 0
        ? `删除完成：成功 ${succeeded} 项，失败 ${failed} 项`
        : `删除完成：成功 ${succeeded} 项`,
      failed > 0 ? 'error' : 'success'
    )
    clearSelection()
    setDeleteDialogOpen(false)
    await fetchVideos()
    const err = useVideoStore.getState().error
    if (err) {
      showToast(`加载失败: ${err}`, 'error')
      useVideoStore.getState().clearError()
    }
  }

  const handleRerun = () => {
    if (!hasSelection) return
    setRerunDialogOpen(true)
  }

  const handleDelete = () => {
    if (!hasSelection) return
    setDeleteDialogOpen(true)
  }

  const handlePackage = async () => {
    if (!hasSelection) return
    await batchPackage(Array.from(selectedIds))
    showToast('打包已提交，完成后将自动下载', 'success')
    exitSelectMode()
  }

  const handleRunToConfirm = async ({
    targetPhase,
    startPhase,
  }: {
    targetPhase: string
    startPhase: string | null
  }) => {
    const result = await batchRunTo(
      Array.from(selectedIds),
      targetPhase,
      startPhase
    )
    const succeeded = result.results.filter(
      (r) => r.status === 'run_to' || r.status === 'rerun_to'
    ).length
    const failed = result.results.length - succeeded
    showToast(
      failed > 0
        ? `运行提交完成：成功 ${succeeded} 项，跳过 ${failed} 项`
        : `运行提交完成：成功 ${succeeded} 项`,
      failed > 0 ? 'error' : 'success'
    )
    setRunToDialogOpen(false)
    exitSelectMode()
    await fetchVideos()
    const err = useVideoStore.getState().error
    if (err) {
      showToast(`加载失败: ${err}`, 'error')
      useVideoStore.getState().clearError()
    }
  }

  const filters: BatchFilter[] = [
    { key: 'all', label: '全选', onClick: selectAllVisible },
    { key: 'unpacked', label: '未打包', onClick: selectUnpacked },
    {
      key: 'approved',
      label: '仅已通过',
      onClick: selectReviewApproved,
    },
    {
      key: 'not-passed',
      label: '未通过/部分通过',
      onClick: selectReviewNotPassed,
    },
    { key: 'clear', label: '取消选择', onClick: clearSelection },
  ]

  const actions: BatchAction[] = [
    { key: 'rerun', label: '重跑', onClick: handleRerun },
    {
      key: 'run-to',
      label: '运行到',
      onClick: () => setRunToDialogOpen(true),
    },
    { key: 'package', label: '打包', onClick: handlePackage },
    { key: 'delete', label: '删除', danger: true, onClick: handleDelete },
  ]

  return (
    <section className="view workbench-view">
      <div className="list-fixed-header">
        <section className="filters-row">
          <md-tabs
            ref={tabsRef}
            active-tab-index={selectedType === 'knowledge' ? 0 : 1}
          >
            <md-primary-tab>知识点</md-primary-tab>
            <md-primary-tab>题目</md-primary-tab>
          </md-tabs>
          <md-outlined-text-field
            type="search"
            placeholder="搜索 ID、标题或内部记录"
            value={searchQuery}
            onInput={(e: React.FormEvent<HTMLElement>) =>
              debouncedSetSearchQuery((e.target as HTMLInputElement).value)
            }
          />
        </section>

        <div className="stats-row">
          <StatCards />
          <div className="stats-actions">
            {!sseConnected && (
              <span
                className="sse-status"
                title="实时连接已断开，正在尝试重连…"
              >
                <md-icon style={{ color: 'var(--md-sys-color-error)' }}>
                  cloud_off
                </md-icon>
              </span>
            )}
            <md-icon-button
              onClick={toggleSelectMode}
              title={selectMode ? '完成' : '多选'}
              className={selectMode ? 'active-icon' : ''}
            >
              <md-icon>{selectMode ? 'close' : 'checklist'}</md-icon>
            </md-icon-button>
            <md-icon-button onClick={openAddDialog} title="添加">
              <md-icon>add</md-icon>
            </md-icon-button>
            <md-icon-button
              onClick={() => setPackageDialogOpen(true)}
              title="包历史"
            >
              <md-icon>inventory_2</md-icon>
            </md-icon-button>
          </div>
        </div>
      </div>
      {selectMode && (
        <BatchToolbar
          selectedCount={count}
          filters={filters}
          actions={actions}
          onExitSelectMode={exitSelectMode}
        />
      )}
      <div className="list-scroll-region">
        <VideoList />
      </div>
      <AddDialog />
      <PackageHistoryDialog
        open={packageDialogOpen}
        onClose={() => setPackageDialogOpen(false)}
      />
      <BatchRerunDialog
        open={rerunDialogOpen}
        videoIds={Array.from(selectedIds)}
        onClose={() => setRerunDialogOpen(false)}
      />
      <BatchDeleteDialog
        open={deleteDialogOpen}
        count={count}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={handleDeleteConfirm}
      />
      <RunToDialog
        open={runToDialogOpen}
        videos={selectedVideos}
        onClose={() => setRunToDialogOpen(false)}
        onConfirm={handleRunToConfirm}
      />
    </section>
  )
}
