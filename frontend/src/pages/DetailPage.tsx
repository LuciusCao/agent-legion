import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDetailStore } from "../stores/detailStore";
import { useArtifactStore } from "../stores/artifactStore";
import { useInteractionStore } from "../stores/interactionStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoStore } from "../stores/videoStore";
import { VideoPlayer } from "../components/VideoPlayer";
import { ChapterStrip } from "../components/ChapterStrip";
import { PhaseRunsPanel } from "../components/PhaseRunsPanel";
import { InteractionOverlay } from "../components/InteractionOverlay";
import { useVideoPhaseEvents } from "../hooks/useVideoPhaseEvents";
import { DetailTabs } from "../components/DetailTabs";
import { SubtitlePanel } from "../components/SubtitlePanel";
import { NodePanel } from "../components/NodePanel";
import { MetadataPanel } from "../components/MetadataPanel";
import { RerunDialog } from "../components/RerunDialog";
import { DeleteDialog } from "../components/DeleteDialog";
import { TYPE_LABELS, PHASE_LABELS, STATUS_LABELS } from "../labels";
import { statusGroup, triggerDownload } from "../helpers";
import { PhaseStepper } from "../components/PhaseStepper";
import { api } from "../api";

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const playerRef = useRef<HTMLVideoElement>(null);
  const detailPrimaryRef = useRef<HTMLDivElement>(null);
  const phaseSidebarRef = useRef<HTMLElement>(null);
  const previousPlaybackTimeRef = useRef<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);

  const {
    currentVideo,
    phaseRuns,
    transcriptionRuns,
    activeTab,
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

  const syncPhaseSidebarMaxHeight = useCallback(() => {
    const primary = detailPrimaryRef.current;
    const sidebar = phaseSidebarRef.current;
    if (!primary || !sidebar) return;

    sidebar.style.setProperty(
      "--detail-primary-height",
      `${primary.getBoundingClientRect().height}px`
    );
  }, []);

  useEffect(() => {
    const primary = detailPrimaryRef.current;
    if (!primary) return;

    const resizeObserver = new ResizeObserver(syncPhaseSidebarMaxHeight);
    resizeObserver.observe(primary);
    syncPhaseSidebarMaxHeight();

    const raf = requestAnimationFrame(syncPhaseSidebarMaxHeight);

    return () => {
      resizeObserver.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [syncPhaseSidebarMaxHeight]);

  useEffect(() => {
    syncPhaseSidebarMaxHeight();
  }, [currentVideo, artifacts.chapters.length, syncPhaseSidebarMaxHeight]);

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

  return (
    <section className="view detail-view">
      <section className="detail-upper">
        <div className="detail-primary" ref={detailPrimaryRef}>
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
                <PhaseStepper video={currentVideo} />
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
            </div>
          </header>

          {currentVideo && (
            <VideoPlayer
              video={currentVideo}
              artifacts={artifacts}
              onTimeUpdate={handleTimeUpdate}
              videoRef={playerRef}
            />
          )}
          <ChapterStrip
            chapters={artifacts.chapters}
            currentTime={currentTime}
            onSeek={handleSeek}
          />
        </div>
        <aside className="phase-runs-sidebar" ref={phaseSidebarRef}>
          <PhaseRunsPanel
            phaseRuns={phaseRuns}
            transcriptionRuns={transcriptionRuns}
            contentType={currentVideo?.content_type}
            currentPhase={currentVideo?.current_phase}
            videoStatus={currentVideo?.status}
          />
        </aside>
      </section>

      <section className="detail-bottom">
        {currentVideo && <DetailTabs contentType={currentVideo.content_type} />}
        <div className="tab-panel">
          {activeTab === "subtitles" && <SubtitlePanel currentTime={currentTime} onSeek={handleSeek} />}
          {activeTab === "nodes" && <NodePanel onSeek={handleSeek} replayInteraction={replayInteraction} />}
          {activeTab === "metadata" && <MetadataPanel />}
        </div>
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
    </section>
  );
}
