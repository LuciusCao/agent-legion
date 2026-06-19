import { useEffect, useState, useCallback } from 'react'
import { Tabs, Tab, TextField, IconButton } from '@mui/material'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import { useVideoEvents } from '../hooks/useVideoEvents'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { getPhases } from '../helpers'
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
import { MaterialIcon } from '../components/MaterialIcon'

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
  const batchRerun = useVideoStore((state) => state.batchRerun)
  const batchRunTo = useVideoStore((state) => state.batchRunTo)
  const batchPackage = useVideoStore((state) => state.batchPackage)
  const exitSelectMode = useVideoStore((state) => state.exitSelectMode)
  const { openAddDialog, closeAddDialog, addDialogOpen, showToast } =
    useUiStore()
  const [packageDialogOpen, setPackageDialogOpen] = useState(false)
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [runToDialogOpen, setRunToDialogOpen] = useState(false)
  const tabValue = selectedType === 'knowledge' ? 0 : 1

  const debouncedSetSearchQuery = useDebouncedCallback(setSearchQuery, 250)

  const handleFetchVideos = useCallback(async () => {
    await fetchVideos()
    const err = useVideoStore.getState().error
    if (err) {
      showToast(`加载失败: ${err}`, 'error')
      useVideoStore.getState().clearError()
    }
  }, [fetchVideos, showToast])

  useEffect(() => {
    handleFetchVideos()
  }, [handleFetchVideos])

  useVideoEvents()

  const handleTabChange = (_: React.SyntheticEvent, value: number) => {
    setSelectedType(value === 0 ? 'knowledge' : 'question')
  }

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
          <Tabs value={tabValue} onChange={handleTabChange}>
            <Tab label="知识点" />
            <Tab label="题目" />
          </Tabs>
          <TextField
            type="search"
            placeholder="搜索 ID、标题或内部记录"
            value={searchQuery}
            onChange={(e) => debouncedSetSearchQuery(e.target.value)}
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
                <MaterialIcon sx={{ color: '#d32f2f' }} name="cloud_off" />
              </span>
            )}
            <IconButton
              onClick={toggleSelectMode}
              title={selectMode ? '完成' : '多选'}
              className={selectMode ? 'active-icon' : ''}
            >
              <MaterialIcon name={selectMode ? 'close' : 'checklist'} />
            </IconButton>
            <IconButton onClick={() => openAddDialog()} title="添加">
              <MaterialIcon name="add" />
            </IconButton>
            <IconButton
              onClick={() => setPackageDialogOpen(true)}
              title="包历史"
            >
              <MaterialIcon name="inventory_2" />
            </IconButton>
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
      <AddDialog
        open={addDialogOpen}
        onClose={closeAddDialog}
        context="video"
      />
      <PackageHistoryDialog
        open={packageDialogOpen}
        onClose={() => setPackageDialogOpen(false)}
      />
      <BatchRerunDialog
        open={rerunDialogOpen}
        items={selectedVideos.map((v) => ({
          id: v.id,
          name: v.external_id || v.title || v.id,
          currentPhase: v.current_phase,
          status: v.status,
        }))}
        phases={getPhases(selectedVideos[0]?.content_type ?? 'knowledge')}
        itemLabel="视频"
        onConfirm={async (ids, phase) => {
          await batchRerun(ids, phase)
          exitSelectMode()
          await fetchVideos()
          const err = useVideoStore.getState().error
          if (err) {
            showToast(`加载失败: ${err}`, 'error')
            useVideoStore.getState().clearError()
          }
        }}
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
