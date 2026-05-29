import { useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useVideoStore } from '../stores/videoStore'
import { PHASE_LABELS, STATUS_LABELS, TYPE_LABELS } from '../labels'
import { statusGroup, filterVideos, formatInteractionStats } from '../helpers'
import { PhaseStepper } from './PhaseStepper'
import { InteractionReviewBadge } from './InteractionReviewBadge'
import styles from './VideoList.module.css'

export function VideoList() {
  const navigate = useNavigate()
  const {
    videos,
    selectedType,
    statusFilter,
    searchQuery,
    packedFilter,
    selectMode,
    packageSelectMode,
    selectedIds,
    toggleVideoSelection,
  } = useVideoStore()

  const filtered = useMemo(() => {
    return filterVideos(videos, {
      selectedType,
      statusFilter,
      searchQuery,
      packedFilter,
    })
  }, [videos, selectedType, statusFilter, searchQuery, packedFilter])

  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 72,
    overscan: 5,
  })

  if (filtered.length === 0) {
    return (
      <div className={styles.videoList}>
        <div className="empty-state">
          <md-icon
            style={{ fontSize: '48px', color: 'var(--md-sys-color-outline)' }}
          >
            inbox
          </md-icon>
          <p className="title-medium">
            暂无{selectedType === 'knowledge' ? '知识点' : '题目'}视频
          </p>
          <p
            className="body-medium"
            style={{ color: 'var(--md-sys-color-outline)' }}
          >
            点击右上角 + 添加视频
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.videoList}>
      <div ref={parentRef} className={styles.scrollContainer}>
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const video = filtered[virtualItem.index]
            const isSelected = selectedIds.has(video.id)
            return (
              <div
                key={virtualItem.key}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${virtualItem.start}px)`,
                }}
              >
                <md-list-item
                  type="button"
                  className={`${isSelected ? 'active' : ''} ${packageSelectMode && video.status !== 'completed' ? 'dimmed' : ''}`}
                  onClick={() => {
                    if (selectMode) {
                      toggleVideoSelection(video.id)
                    } else if (packageSelectMode) {
                      if (video.status === 'completed') {
                        toggleVideoSelection(video.id)
                      }
                    } else {
                      navigate(`/videos/${video.id}`)
                    }
                  }}
                >
                  {(selectMode || packageSelectMode) && (
                    <md-checkbox
                      slot="start"
                      disabled={
                        (packageSelectMode && video.status !== 'completed') ||
                        undefined
                      }
                      checked={isSelected || undefined}
                      onClick={(e: React.MouseEvent) => {
                        e.stopPropagation()
                        toggleVideoSelection(video.id)
                      }}
                    />
                  )}
                  <div slot="headline">
                    <strong>{video.title || '未命名'}</strong>
                  </div>
                  <div slot="supporting-text">
                    <small>
                      {TYPE_LABELS[video.content_type]} ·{' '}
                      {video.external_id || '未填 ID'}
                    </small>
                    {video.error_message && (
                      <small className="error-text" title={video.error_message}>
                        {video.error_message}
                      </small>
                    )}
                  </div>
                  <div slot="end" className={styles.statusEnd}>
                    {video.content_type === 'knowledge' &&
                      video.interaction_stats && (
                        <span
                          className={styles.interactionStats}
                          title="互动节点通过状态"
                        >
                          {formatInteractionStats(video.interaction_stats)}
                        </span>
                      )}
                    <span
                      className={`phase-name ${video.status === 'running' ? 'running' : ''}`}
                    >
                      {PHASE_LABELS[video.current_phase]}
                    </span>
                    <PhaseStepper video={video} />
                    <span className={`status-badge ${statusGroup(video)}`}>
                      {STATUS_LABELS[statusGroup(video)] || video.status}
                    </span>
                    {video.content_type === 'knowledge' &&
                      video.status === 'completed' && (
                        <InteractionReviewBadge
                          status={video.interaction_review_status}
                        />
                      )}
                    {!!video.packed && <span className="packed-badge">已打包</span>}
                  </div>
                </md-list-item>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
