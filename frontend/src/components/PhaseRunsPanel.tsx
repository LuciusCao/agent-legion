import type { CSSProperties } from "react";
import type { PhaseRun, TranscriptionRun, ContentType, VideoItem } from "../types";
import { STATUS_LABELS, STATUS_ICONS } from "../labels";
import { usePhaseRunsTimeline } from "../hooks/usePhaseRunsTimeline";
import { PhaseStepper } from "./PhaseStepper";
import { TranscriptionDetails } from "./TranscriptionDetails";
import styles from "./PhaseRunsPanel.module.css";


interface PhaseRunsPanelProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
  video?: VideoItem | null;
  contentType?: ContentType;
  currentPhase?: string;
  videoStatus?: string;
}

const NODE_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
};

const CONTENT_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
};

const BADGE_STATUS_CLASS: Record<string, string> = {
  completed: styles.completed,
  running: styles.running,
  failed: styles.failed,
  pending: styles.pending,
  queued: styles.queued,
};

export function PhaseRunsPanel({
  phaseRuns,
  transcriptionRuns,
  video,
  contentType,
  currentPhase,
  videoStatus,
}: PhaseRunsPanelProps) {
  const {
    viewMode, setViewMode, expandedDetails, toggleDetail,
    sessionLogs, openSession, sessionDialog, setSessionDialog,
    transcriptionDialogOpen, setTranscriptionDialogOpen,
    items, sortedTrans, transPrimary, transFallback,
    formatDuration, extractOpenClawArg,
  } = usePhaseRunsTimeline(phaseRuns, transcriptionRuns, contentType, currentPhase, videoStatus);

  const dialogStyle = {
    "--md-dialog-container-color": "#ffffff",
    maxWidth: "760px",
    width: "90vw",
  } as CSSProperties;

  return (
    <div className={styles.phaseRunsPanel}>
      {video && (
        <div className={styles.panelStepper}>
          <PhaseStepper video={video} />
          <md-text-button onClick={() => setViewMode((v) => (v === "latest" ? "history" : "latest"))}>
            {viewMode === "latest" ? "历史" : "当前"}
          </md-text-button>
        </div>
      )}

      {items.length === 0 && <p className="empty-state">暂无处理记录</p>}

      <div className={styles.phaseTimeline}>
        <div className={styles.phaseTimelineItems}>
          {items.map((item, idx) => {
            const icon = STATUS_ICONS[item.run.status] || "help";
            const statusText = STATUS_LABELS[item.run.status] || item.run.status;
            const hasError = !!item.run.error_message;
            const isTranscribe = item.run.phase_key === "transcribe";
            const hasTransDetails = isTranscribe && transcriptionRuns.length > 0;
            const isDetailExpanded = expandedDetails.has(item.run.id);
            const sessionId = item.run.agent_session_id || extractOpenClawArg(item.run.command_json, "--session-id");
            const hasAgentSession = !!sessionId && item.run.id > 0;

            return (
              <div key={item.run.id} className={styles.phaseTimelineItem}>
                <div className={styles.timelineLeft}>
                  <div
                    className={`${styles.timelineNode} ${NODE_STATUS_CLASS[item.run.status] || ""} ${
                      item.run.status === "running" ? styles.spinning : ""
                    }`}
                  >
                    <md-icon>{icon}</md-icon>
                  </div>
                  {idx < items.length - 1 && <div className={styles.timelineLine} />}
                </div>

                <div className={`${styles.timelineContent} ${CONTENT_STATUS_CLASS[item.run.status] || ""}`}>
                  <div className={styles.timelineHeader}>
                    <span className={styles.timelineName}>
                      {item.label}
                      {item.occurrence && item.occurrence > 1 ? (
                        <span className={styles.occurrenceBadge}> 第{item.occurrence}次</span>
                      ) : null}
                    </span>
                    <span className={`${styles.timelineStatusBadge} ${BADGE_STATUS_CLASS[item.run.status] || ""}`}>{statusText}</span>
                  </div>

                  {item.tool && (
                    <div className={styles.timelineMeta}>
                      <md-icon className={styles.metaIcon}>build_circle</md-icon>
                      <span className={styles.timelineTool}>{item.tool}</span>
                    </div>
                  )}

                  <div className={styles.timelineTimes}>
                    <span>
                      <md-icon className={styles.metaIcon}>schedule</md-icon>
                      排队 {formatDuration(item.queueTime)}
                    </span>
                    <span>
                      <md-icon className={styles.metaIcon}>timer</md-icon>
                      处理 {formatDuration(item.processTime)}
                    </span>
                  </div>

                  {hasAgentSession && (
                    <div className={styles.timelineMeta}>
                      <button className={styles.inlineAction} onClick={() => void openSession(item.run, sessionId)}>
                        <md-icon className={styles.toggleIcon}>forum</md-icon>
                        查看会话
                      </button>
                    </div>
                  )}

                  {hasError && (
                    <button className={styles.timelineDetailToggle} onClick={() => toggleDetail(item.run.id)}>
                      <md-icon className={styles.toggleIcon}>error</md-icon>
                      错误详情
                      <md-icon className={styles.toggleIcon}>
                        {isDetailExpanded ? "expand_less" : "expand_more"}
                      </md-icon>
                    </button>
                  )}

                  {hasTransDetails && (
                    <button className={styles.timelineDetailToggle} onClick={() => setTranscriptionDialogOpen(true)}>
                      <md-icon className={styles.toggleIcon}>text_fields</md-icon>
                      转录详情
                    </button>
                  )}

                  {isDetailExpanded && hasError && (
                    <div className="timeline-detail-content error">{item.run.error_message}</div>
                  )}

                </div>
              </div>
            );
          })}
        </div>
      </div>

      {transcriptionDialogOpen && (
        <md-dialog open onClosed={() => setTranscriptionDialogOpen(false)} style={dialogStyle}>
          <div slot="headline">转录详情</div>
          <div slot="content">
            <TranscriptionDetails
              primary={transPrimary}
              fallback={transFallback}
              totalCount={transcriptionRuns.length}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={() => setTranscriptionDialogOpen(false)}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {sessionDialog && (
        <md-dialog open onClosed={() => setSessionDialog(null)} style={dialogStyle}>
          <div slot="headline">Agent 会话</div>
          <div slot="content" className={styles.dialogContent}>
            <div className={styles.sessionKey}>会话 {sessionDialog.sessionId}</div>
            <pre className={styles.sessionPreview}>{sessionLogs[sessionDialog.runId] || "加载中..."}</pre>
          </div>
          <div slot="actions">
            <md-text-button onClick={() => setSessionDialog(null)}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}
    </div>
  );
}
