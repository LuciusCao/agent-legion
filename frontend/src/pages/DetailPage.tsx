import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDetailStore } from "../stores/detailStore";
import { useArtifactStore } from "../stores/artifactStore";
import { useInteractionStore } from "../stores/interactionStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoStore } from "../stores/videoStore";
import { VideoPlayer } from "../components/VideoPlayer";
import { TimelineStrip } from "../components/TimelineStrip";
import { PhaseRunsPanel } from "../components/PhaseRunsPanel";
import { InteractionOverlay } from "../components/InteractionOverlay";
import { useVideoPhaseEvents } from "../hooks/useVideoPhaseEvents";
import { SubtitlePanel } from "../components/SubtitlePanel";
import { NodePanel } from "../components/NodePanel";
import { MetadataPanel } from "../components/MetadataPanel";
import { RerunDialog } from "../components/RerunDialog";
import { DeleteDialog } from "../components/DeleteDialog";
import { TYPE_LABELS, PHASE_LABELS, STATUS_LABELS } from "../labels";
import { statusGroup, triggerDownload } from "../helpers";
import { api } from "../api";

type MoreDialogType = "subtitles" | "nodes" | "metadata" | null;

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const playerRef = useRef<HTMLVideoElement>(null);
  const previousPlaybackTimeRef = useRef<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [moreDialogOpen, setMoreDialogOpen] = useState(false);
  const [moreDialogType, setMoreDialogType] = useState<MoreDialogType>(null);

  const {
    currentVideo,
    phaseRuns,
    transcriptionRuns,
    loadVideo,
    loadLog,
  } = useDetailStore();

  const {
    artifacts,
    loadArtifacts,
  } = useArtifactStore();

  const {
    triggeredNodeIndexes,
    dismissedNodeIndexes,
    currentSentence,
    triggerInteraction,
    dismissInteraction,
    replayInteraction,
    pushWord,
    resetSentence,
    clearSentence,
  } = useInteractionStore();

  const { openRerunDialog, openDeleteDialog, showToast } = useUiStore();
  const { fetchVideos } = useVideoStore();

  const checkFetchError = () => {
    const err = useVideoStore.getState().error;
    if (err) {
      showToast(`加载失败: ${err}`, "error");
      useVideoStore.getState().clearError();
    }
  };

  useVideoPhaseEvents(id);

  useEffect(() => {
    if (!id) return;
    loadVideo(id);
    loadArtifacts(id);
    loadLog(id);
  }, [id, loadVideo, loadArtifacts, loadLog]);

  const prevPhaseRef = useRef<string | null>(null);
  const prevStatusRef = useRef<string | null>(null);

  useEffect(() => {
    if (!id || !currentVideo) return;
    const currentPhase = currentVideo.current_phase;
    const currentStatus = currentVideo.status;
    if (
      prevPhaseRef.current !== null &&
      (prevPhaseRef.current !== currentPhase || prevStatusRef.current !== currentStatus)
    ) {
      loadArtifacts(id);
      loadLog(id);
    }
    prevPhaseRef.current = currentPhase;
    prevStatusRef.current = currentStatus;
  }, [id, currentVideo, loadArtifacts, loadLog]);

  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time);
      const player = playerRef.current;
      if (!player) return;
      const previousTime = previousPlaybackTimeRef.current;

      artifacts.interactions.forEach((node, index) => {
        const trigger = Number(node.trigger_time ?? 0);
        if (!Number.isFinite(trigger)) return;

        const crossedTrigger =
          previousTime !== null && previousTime < trigger && time >= trigger;
        const reachedTriggerWindow = time >= trigger && time < trigger + 1.5;
        if (
          !triggeredNodeIndexes.has(index) &&
          !dismissedNodeIndexes.has(index) &&
          !player.paused &&
          (crossedTrigger || reachedTriggerWindow)
        ) {
          player.pause();
          triggerInteraction(index);
        }
      });
      previousPlaybackTimeRef.current = time;
    },
    [artifacts.interactions, triggeredNodeIndexes, dismissedNodeIndexes, triggerInteraction]
  );

  const handleSeek = useCallback((time: number) => {
    if (playerRef.current) playerRef.current.currentTime = time;
  }, []);

  const activeNodeIndex = Array.from(triggeredNodeIndexes).pop();
  const activeNode = activeNodeIndex !== undefined ? artifacts.interactions[activeNodeIndex] : null;
  const detailTitle = currentVideo?.title || "未选择资源";

  const handleContinue = useCallback(() => {
    clearSentence();
    if (activeNodeIndex !== undefined) {
      dismissInteraction(activeNodeIndex);
    }
    playerRef.current?.play();
  }, [clearSentence, activeNodeIndex, dismissInteraction]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!id) return false;
    try {
      await api(`/api/videos/${id}`, { method: "DELETE" });
      showToast("删除成功", "success");
      await fetchVideos();
      checkFetchError();
      navigate("/");
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      showToast(`删除失败: ${message}`, "error");
      return false;
    }
  }, [id, navigate, fetchVideos, showToast]);

  const handlePackage = useCallback(async () => {
    if (!id) return;
    const result = await api<{ download_url: string }>("/api/package", {
      method: "POST",
      body: JSON.stringify({ video_ids: [id] }),
    });
    await Promise.all([fetchVideos(), loadVideo(id)]);
    checkFetchError();
    await triggerDownload(result.download_url);
  }, [id, fetchVideos, loadVideo]);

  const handleRerun = useCallback(
    async (phase: string) => {
      if (!id) return;
      try {
        await api(`/api/videos/${id}/rerun`, { method: "POST", body: JSON.stringify({ phase }) });
        showToast("重跑已提交", "success");
        await Promise.all([fetchVideos(), loadVideo(id), loadLog(id)]);
        checkFetchError();
      } catch (err) {
        if (err instanceof Error && err.message.includes("currently being processed")) {
          showToast("该资源正在被处理中，请等待当前阶段完成后再重跑。", "error");
        } else {
          throw err;
        }
      }
    },
    [id, fetchVideos, loadVideo, loadLog, showToast]
  );

  const openMoreDialog = (type: MoreDialogType) => {
    setMoreDialogOpen(false);
    setMoreDialogType(type);
  };

  const closeMoreDialog = () => {
    setMoreDialogOpen(false);
    setMoreDialogType(null);
  };

  return (
    <section className="view detail-view">
      <section className="detail-upper">
        <header className="detail-topbar">
          <md-icon-button onClick={() => navigate("/")}>
            <md-icon>arrow_back</md-icon>
          </md-icon-button>
          <div className="detail-title-block" data-tooltip={detailTitle}>
            <h1>{detailTitle}</h1>
            {currentVideo && (
              <p>
                {TYPE_LABELS[currentVideo.content_type]} · {currentVideo.external_id || "未填 ID"}
              </p>
            )}
            {currentVideo?.error_message && (
              <p className="error-text" style={{ marginTop: 4 }}>{currentVideo.error_message}</p>
            )}
          </div>
          {currentVideo && (
            <div className="detail-progress">
              <span className={`phase-name ${currentVideo.status === "running" ? "running-text" : ""}`}>
                {PHASE_LABELS[currentVideo.current_phase]}
              </span>
              <span className={`status-badge ${statusGroup(currentVideo)}`}>
                {STATUS_LABELS[statusGroup(currentVideo)] || currentVideo.status}
              </span>
              {!!currentVideo.packed && <span className="packed-badge">已打包</span>}
            </div>
          )}
          <div className="detail-actions">
            <md-icon-button onClick={openRerunDialog} title="重跑">
              <md-icon>restart_alt</md-icon>
            </md-icon-button>
            <md-icon-button
              disabled={(!currentVideo || currentVideo.status !== "completed") || undefined}
              onClick={handlePackage}
              title="打包"
            >
              <md-icon>inventory_2</md-icon>
            </md-icon-button>
            <md-icon-button style={{ color: "var(--md-sys-color-error)" }} onClick={openDeleteDialog} title="删除">
              <md-icon>delete</md-icon>
            </md-icon-button>
            <md-icon-button id="more-menu-btn" onClick={() => setMoreDialogOpen(true)} title="更多">
              <md-icon>more_vert</md-icon>
            </md-icon-button>
          </div>
        </header>

        <div className="detail-primary">
          {currentVideo && (
            <VideoPlayer
              video={currentVideo}
              artifacts={artifacts}
              onTimeUpdate={handleTimeUpdate}
              videoRef={playerRef}
            />
          )}

          {currentVideo && (
            <TimelineStrip
              chapters={artifacts.chapters}
              interactions={artifacts.interactions}
              currentTime={currentTime}
              onSeek={handleSeek}
              onReplayInteraction={replayInteraction}
            />
          )}
        </div>
        <aside className="phase-runs-sidebar">
          <PhaseRunsPanel
            phaseRuns={phaseRuns}
            transcriptionRuns={transcriptionRuns}
            video={currentVideo}
            contentType={currentVideo?.content_type}
            currentPhase={currentVideo?.current_phase}
            videoStatus={currentVideo?.status}
          />
        </aside>
      </section>

      <InteractionOverlay
        node={activeNode}
        currentSentence={currentSentence}
        onWordClick={pushWord}
        onReset={resetSentence}
        onContinue={handleContinue}
      />

      <RerunDialog video={currentVideo} onConfirm={handleRerun} />
      <DeleteDialog onConfirm={handleDeleteConfirm} />

      {moreDialogOpen && (
        <md-dialog
          open
          onClosed={closeMoreDialog}
          style={{ "--md-dialog-container-color": "#ffffff" } as React.CSSProperties}
        >
          <div slot="headline">更多信息</div>
          <div slot="content" style={{ display: "flex", flexDirection: "column", gap: "8px", minWidth: "200px" }}>
            <md-text-button
              style={{ justifyContent: "flex-start" }}
              onClick={() => openMoreDialog("subtitles")}
            >
              <md-icon slot="icon">subtitles</md-icon>
              字幕
            </md-text-button>
            {currentVideo?.content_type === "knowledge" && (
              <md-text-button
                style={{ justifyContent: "flex-start" }}
                onClick={() => openMoreDialog("nodes")}
              >
                <md-icon slot="icon">account_tree</md-icon>
                交互节点
              </md-text-button>
            )}
            <md-text-button
              style={{ justifyContent: "flex-start" }}
              onClick={() => openMoreDialog("metadata")}
            >
              <md-icon slot="icon">data_object</md-icon>
              元数据
            </md-text-button>
          </div>
          <div slot="actions">
            <md-text-button onClick={closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {moreDialogType === "nodes" && (
        <md-dialog
          open
          onClosed={closeMoreDialog}
          style={{ "--md-dialog-container-color": "#ffffff", maxWidth: "760px", width: "90vw" } as React.CSSProperties}
        >
          <div slot="headline">交互节点</div>
          <div slot="content" style={{ maxHeight: "60vh", overflow: "auto", padding: "8px 0" }}>
            <NodePanel
              onSeek={(time) => {
                handleSeek(time);
                closeMoreDialog();
              }}
              replayInteraction={replayInteraction}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {moreDialogType === "subtitles" && (
        <md-dialog
          open
          onClosed={closeMoreDialog}
          style={{ "--md-dialog-container-color": "#ffffff", maxWidth: "720px", width: "90vw" } as React.CSSProperties}
        >
          <div slot="headline">字幕</div>
          <div slot="content" style={{ maxHeight: "60vh", overflow: "auto", padding: "8px 0" }}>
            <SubtitlePanel currentTime={currentTime} onSeek={(time) => {
              handleSeek(time);
              closeMoreDialog();
            }} />
          </div>
          <div slot="actions">
            <md-text-button onClick={closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}

      {moreDialogType === "metadata" && (
        <md-dialog
          open
          onClosed={closeMoreDialog}
          style={{ "--md-dialog-container-color": "#ffffff", maxWidth: "640px", width: "90vw" } as React.CSSProperties}
        >
          <div slot="headline">元数据</div>
          <div slot="content" style={{ maxHeight: "60vh", overflow: "auto" }}>
            <MetadataPanel />
          </div>
          <div slot="actions">
            <md-text-button onClick={closeMoreDialog}>关闭</md-text-button>
          </div>
        </md-dialog>
      )}
    </section>
  );
}
