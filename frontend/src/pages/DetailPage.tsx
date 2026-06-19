import {
  IconButton,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material'
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
import { MaterialIcon } from '../components/MaterialIcon'
import { TYPE_LABELS, STATUS_LABELS } from '../labels'
import { statusGroup } from '../helpers'
import { useDetailPage } from '../hooks/useDetailPage'

export function DetailPage() {
  const hook = useDetailPage()

  return (
    <section className="view detail-view">
      <section className="detail-upper">
        <header className="detail-topbar">
          <IconButton onClick={() => window.history.back()} aria-label="返回">
            <MaterialIcon name="arrow_back" />
          </IconButton>
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
                      status={
                        hook.video.interaction_review_status ?? 'all_failed'
                      }
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
            <IconButton onClick={hook.openRerunDialog} title="重跑">
              <MaterialIcon name="restart_alt" />
            </IconButton>
            <IconButton
              onClick={() => hook.setRunToDialogOpen(true)}
              title="运行到"
            >
              <MaterialIcon name="play_circle" />
            </IconButton>
            <IconButton
              disabled={!hook.video || hook.video.status !== 'completed'}
              onClick={hook.handlePackage}
              title="打包"
            >
              <MaterialIcon name="inventory_2" />
            </IconButton>
            <IconButton
              sx={{ color: '#d32f2f' }}
              onClick={hook.openDeleteDialog}
              title="删除"
            >
              <MaterialIcon name="delete" />
            </IconButton>
            <IconButton
              id="more-menu-btn"
              onClick={() => hook.setMoreDialogOpen(true)}
              title="更多"
            >
              <MaterialIcon name="more_vert" />
            </IconButton>
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

      <Dialog
        open={hook.moreDialogOpen}
        onClose={hook.closeMoreDialog}
        PaperProps={{ sx: { minWidth: 200 } }}
      >
        <DialogTitle>更多信息</DialogTitle>
        <DialogContent
          sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}
        >
          <Button
            variant="text"
            sx={{ justifyContent: 'flex-start' }}
            startIcon={<MaterialIcon name="subtitles" />}
            onClick={() => hook.openMoreDialog('subtitles')}
          >
            字幕
          </Button>
          {hook.video?.content_type === 'knowledge' && (
            <Button
              variant="text"
              sx={{ justifyContent: 'flex-start' }}
              startIcon={<MaterialIcon name="account_tree" />}
              onClick={() => hook.openMoreDialog('nodes')}
            >
              交互节点
            </Button>
          )}
          <Button
            variant="text"
            sx={{ justifyContent: 'flex-start' }}
            startIcon={<MaterialIcon name="data_object" />}
            onClick={() => hook.openMoreDialog('metadata')}
          >
            元数据
          </Button>
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={hook.closeMoreDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={hook.moreDialogType === 'nodes'}
        onClose={hook.closeMoreDialog}
        PaperProps={{ sx: { maxWidth: 760, width: '90vw' } }}
      >
        <DialogTitle>交互节点</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto', py: 1 }}>
          <NodePanel
            onSeek={(time) => {
              hook.handleSeek(time)
              hook.closeMoreDialog()
            }}
            replayInteraction={hook.replayInteraction}
          />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={hook.closeMoreDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={hook.moreDialogType === 'subtitles'}
        onClose={hook.closeMoreDialog}
        PaperProps={{ sx: { maxWidth: 720, width: '90vw' } }}
      >
        <DialogTitle>字幕</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto', py: 1 }}>
          <SubtitlePanel
            currentTime={hook.currentTime}
            onSeek={(time) => {
              hook.handleSeek(time)
              hook.closeMoreDialog()
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={hook.closeMoreDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={hook.moreDialogType === 'metadata'}
        onClose={hook.closeMoreDialog}
        PaperProps={{ sx: { maxWidth: 640, width: '90vw' } }}
      >
        <DialogTitle>元数据</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto' }}>
          <MetadataPanel />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={hook.closeMoreDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>
    </section>
  )
}
