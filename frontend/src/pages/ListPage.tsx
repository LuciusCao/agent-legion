import { useEffect, useRef, useCallback, useState } from 'react'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import { useVideoEvents } from '../hooks/useVideoEvents'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { StatCards } from '../components/StatCards'
import { VideoList } from '../components/VideoList'
import { BatchToolbar } from '../components/BatchToolbar'
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
  const { openAddDialog, showToast } = useUiStore()
  const [packageDialogOpen, setPackageDialogOpen] = useState(false)

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
      <BatchToolbar />
      <div className="list-scroll-region">
        <VideoList />
      </div>
      <AddDialog />
      <PackageHistoryDialog
        open={packageDialogOpen}
        onClose={() => setPackageDialogOpen(false)}
      />
    </section>
  )
}
