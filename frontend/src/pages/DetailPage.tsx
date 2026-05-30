import { VideoPlayer } from '../components/VideoPlayer'
import { TimelineStrip } from '../components/TimelineStrip'
import { PhaseRunsPanel } from '../components/PhaseRunsPanel'
import { SubtitlePanel } from '../components/SubtitlePanel'
import { NodePanel } from '../components/NodePanel'
import { MetadataPanel } from '../components/MetadataPanel'
import { RerunDialog } from '../components/RerunDialog'
import { RunToDialog } from '../components/RunToDialog'
import { InteractionReviewBadge } from '../components/InteractionReviewBadge'
import { DeleteDialog } from '../components/DeleteDialog'
import { TYPE_LABELS, STATUS_LABELS } from '../labels'
import { statusGroup } from '../helpers'
import { useDetailPage } from '../hooks/useDetailPage'

export function DetailPage() {
  const hook = useDetailPage()

  return (
    <section className="view detail-view">
      <section className="detail-upper">
        <header className="detail-topbar">
          <md-icon-button onClick={() => window.history.back()}>
            <md-icon>arrow_back</md-icon>
          </md-icon-button>
          <div className="detail-title-block" data-tooltip={hook.detailTitle}>
            <h1>{hook.detailTitle}</h1>
            {hook.video && (
              <p className="detail-meta-line">
                <span>
                  {TYPE_LABELS[hook.video.content_type]} ·{' '}
                  {hook.video.external_id || '未填 ID'}
                </span>
                <span className={`status-badge ${statusGroup(hook.video)}`}>
                  {STATUS_LABELS[statusGroup(hook.video)] || hook.video.status}
                </span>
                {hook.video.content_type === 'knowledge' &&
                  hook.video.status === 'completed' && (
                    <InteractionReviewBadge
                      status={hook.video.interaction_review_status}
                    />
                  )}
                {!!hook.video.packed && (
                  <span className="packed-badge">已打包</span>
                )}
              </p>
            )}
            {hook.video?.error_message && (
              <p className="error-text" style={{ marginTop: 4 }}>
                {hook.video.error_message}
              </p>
            )}
          </div>
          <div className="detail-actions">
            <md-icon-button onClick={hook.openRerunDialog} title="重跑">
              <md-icon>restart_alt</md-icon>
            </md-icon-button>
            <md-icon-button
              onClick={() => hook.setRunToDialogOpen(true)}
              title="运行到"
            >
              <md-icon>play_circle</md-icon>
            </md-icon-button>
            <md-icon-button
              disabled={
                !hook.video || hook.video.status !== 'completed' || undefined
              }
              onClick={hook.handlePackage}
              title="打包"
            >
              <md-icon>inventory_2</md-icon>
            </md-icon-button>
            <md-icon-button
              style={{ color: 'var(--md-sys-color-error)' }}
              onClick={hook.openDeleteDialog}
              title="删除"
            >
              <md-icon>delete</md-icon>
            </md-icon-button>
            <md-icon-button
              id="more-menu-btn"
              onClick={() => hook.setMoreDialogOpen(true)}
              title="更多"
            >
              <md-icon>more_vert</md-icon>
            </md-icon-button>
          </div>
        </header>

        <div className="detail-primary">
          {hook.video && (
            <VideoPlayer
              video={hook.video}
              artifacts={hook.artifacts}
              onTimeUpdate={hook.handleTimeUpdate}
              videoRef={hook.playerRef}
              interactionNode={hook.activeNode}
              interactionSentence={hook.currentSentence}
              onInteractionWordClick={hook.pushWord}
              onInteractionReset={hook.resetSentence}
              onInteractionContinue={hook.handleContinue}
              onPlay={() => hook.setIsPlaying(true)}
              onPause={() => hook.setIsPlaying(false)}
            />
          )}

          {hook.video && (
            <TimelineStrip
              chapters={hook.artifacts.chapters}
              interactions={hook.artifacts.interactions}
              currentTime={hook.currentTime}
              onSeek={hook.handleSeek}
              onReplayInteraction={hook.replayInteraction}
            />
          )}
        </div>
        <aside className="phase-runs-sidebar">
          <PhaseRunsPanel
            phaseRuns={hook.phaseRuns}
            transcriptionRuns={hook.transcriptionRuns}
            video={hook.video}
            contentType={hook.video?.content_type}
            currentPhase={hook.video?.current_phase}
            videoStatus={hook.video?.status}
          />
        </aside>
      </section>

      <RerunDialog video={hook.video} onConfirm={hook.handleRerun} />
      <RunToDialog
        open={hook.runToDialogOpen}
        videos={hook.video ? [hook.video] : []}
        onClose={() => hook.setRunToDialogOpen(false)}
        onConfirm={hook.handleRunTo}
      />
      <DeleteDialog onConfirm={hook.handleDeleteConfirm} />

      {hook.moreDialogOpen && (
        <md-dialog
          open
          onClosed={hook.closeMoreDialog}
          style={
            { '--md-dialog-container-color': '#ffffff' } as React.CSSProperties
          }
        >
          <div slot="headline">更多信息</div>
          <div
            slot="content"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              minWidth: '200px',
            }}
          >
            <md-text-button
              style={{ justifyContent: 'flex-start' }}
              onClick={() => hook.openMoreDialog('subtitles')}
            >
              <md-icon slot="icon">subtitles</md-icon>
              字幕
            </md-text-button>
            {hook.video?.content_type === 'knowledge' && (
              <md-text-button
                style={{ justifyContent: 'flex-start' }}
                onClick={() => hook.openMoreDialog('nodes')}
              >
                <md-icon slot="icon">account_tree</md-icon>
                交互节点
              </md-text-button>
            )}
            <md-text-button
              style={{ justifyContent: 'flex-start' }}
              onClick={() => hook.openMoreDialog('metadata')}
            >
              <md-icon slot="icon">data_object</md-icon>
              元数据
            </md-text-button>
          </div>
          <div slot="actions">
            <md-text-button onClick={hook.closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {hook.moreDialogType === 'nodes' && (
        <md-dialog
          open
          onClosed={hook.closeMoreDialog}
          style={
            {
              '--md-dialog-container-color': '#ffffff',
              maxWidth: '760px',
              width: '90vw',
            } as React.CSSProperties
          }
        >
          <div slot="headline">交互节点</div>
          <div
            slot="content"
            style={{ maxHeight: '60vh', overflow: 'auto', padding: '8px 0' }}
          >
            <NodePanel
              onSeek={(time) => {
                hook.handleSeek(time)
                hook.closeMoreDialog()
              }}
              replayInteraction={hook.replayInteraction}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={hook.closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {hook.moreDialogType === 'subtitles' && (
        <md-dialog
          open
          onClosed={hook.closeMoreDialog}
          style={
            {
              '--md-dialog-container-color': '#ffffff',
              maxWidth: '720px',
              width: '90vw',
            } as React.CSSProperties
          }
        >
          <div slot="headline">字幕</div>
          <div
            slot="content"
            style={{ maxHeight: '60vh', overflow: 'auto', padding: '8px 0' }}
          >
            <SubtitlePanel
              currentTime={hook.currentTime}
              onSeek={(time) => {
                hook.handleSeek(time)
                hook.closeMoreDialog()
              }}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={hook.closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {hook.moreDialogType === 'metadata' && (
        <md-dialog
          open
          onClosed={hook.closeMoreDialog}
          style={
            {
              '--md-dialog-container-color': '#ffffff',
              maxWidth: '640px',
              width: '90vw',
            } as React.CSSProperties
          }
        >
          <div slot="headline">元数据</div>
          <div slot="content" style={{ maxHeight: '60vh', overflow: 'auto' }}>
            <MetadataPanel />
          </div>
          <div slot="actions">
            <md-text-button onClick={hook.closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}
    </section>
  )
}
