import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDetailStore } from "../stores/detailStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoStore } from "../stores/videoStore";
import { VideoPlayer } from "../components/VideoPlayer";
import { ChapterStrip } from "../components/ChapterStrip";
import { PhaseRunsPanel } from "../components/PhaseRunsPanel";
import { InteractionOverlay } from "../components/InteractionOverlay";
import { useVideoPhaseEvents } from "../hooks/useVideoPhaseEvents";
import { DetailTabs } from "../components/DetailTabs";
import { SubtitlePanel } from "../components/SubtitlePanel";
import { ChapterPanel } from "../components/ChapterPanel";
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
  const previewMainRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const [currentTime, setCurrentTime] = useState(0);

  const {
    currentVideo,
    artifacts,
    phaseRuns,
    transcriptionRuns,
    activeTab,
    triggeredNodeIndexes,
    dismissedNodeIndexes,
    currentSentence,
    loadVideo,
    loadArtifacts,
    loadLog,
    triggerInteraction,
    dismissInteraction,
    replayInteraction,
    pushWord,
    resetSentence,
    clearSentence,
  } = useDetailStore();

  const { openRerunDialog, openDeleteDialog, showToast } = useUiStore();
  const { fetchVideos } = useVideoStore();

  useVideoPhaseEvents(id);

  useEffect(() => {
    if (!id) return;
    loadVideo(id);
    loadArtifacts(id);
    loadLog(id);
  }, [id, loadVideo, loadArtifacts, loadLog]);

  const syncHeight = useCallback(() => {
    const main = previewMainRef.current;
    const sidebar = sidebarRef.current;
    if (!main || !sidebar) return;
    sidebar.style.maxHeight = `${main.getBoundingClientRect().height}px`;
  }, []);

  useEffect(() => {
    const main = previewMainRef.current;
    if (!main) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === main) {
          syncHeight();
        }
      }
    });

    syncHeight();
    observer.observe(main);
    // Double rAF to catch Material Web Component async upgrades
    let innerRaf = 0;
    const outerRaf = requestAnimationFrame(() => {
      syncHeight();
      innerRaf = requestAnimationFrame(syncHeight);
    });
    return () => {
      observer.disconnect();
      cancelAnimationFrame(outerRaf);
      cancelAnimationFrame(innerRaf);
    };
  }, [syncHeight]);

  // Re-sync when content that affects left-side height changes
  useEffect(() => {
    syncHeight();
  }, [currentVideo, artifacts.chapters.length, syncHeight]);

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

      const subtitle = artifacts.subtitles.find((s) => time >= s.start && time < s.end);
      const overlay = document.getElementById("subtitleOverlay");
      if (overlay) overlay.textContent = subtitle?.text ?? "";

      artifacts.interactions.forEach((node, index) => {
        const trigger = Number(node.trigger_time ?? 0);
        if (!triggeredNodeIndexes.has(index) && !dismissedNodeIndexes.has(index) && !player.paused && time >= trigger && time < trigger + 1.5) {
          player.pause();
          triggerInteraction(index);
        }
      });
    },
    [artifacts, triggeredNodeIndexes, dismissedNodeIndexes, triggerInteraction]
  );

  const handleSeek = useCallback((time: number) => {
    if (playerRef.current) playerRef.current.currentTime = time;
  }, []);

  const activeNodeIndex = Array.from(triggeredNodeIndexes).pop();
  const activeNode = activeNodeIndex !== undefined ? artifacts.interactions[activeNodeIndex] : null;

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
    triggerDownload(result.download_url);
  }, [id, fetchVideos, loadVideo]);

  const handleRerun = useCallback(
    async (phase: string) => {
      if (!id) return;
      try {
        await api(`/api/videos/${id}/rerun`, { method: "POST", body: JSON.stringify({ phase }) });
        showToast("重跑已提交", "success");
        await Promise.all([fetchVideos(), loadVideo(id), loadLog(id)]);
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
      <header className="detail-topbar">
        <md-icon-button onClick={() => navigate("/")}>
          <md-icon>arrow_back</md-icon>
        </md-icon-button>
        <div className="detail-title-block">
          <h1>{currentVideo?.title || "未选择资源"}</h1>
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
            disabled={(!currentVideo || currentVideo.status !== "completed" || !!currentVideo.packed) || undefined}
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

      <section className="preview-layout">
        <div className="preview-main" ref={previewMainRef}>
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
        <aside className="phase-runs-sidebar" ref={sidebarRef}>
          <PhaseRunsPanel phaseRuns={phaseRuns} transcriptionRuns={transcriptionRuns} contentType={currentVideo?.content_type} />
        </aside>
      </section>

      <section className="detail-bottom">
        {currentVideo && <DetailTabs contentType={currentVideo.content_type} />}
        <div className="tab-panel">
          {activeTab === "subtitles" && <SubtitlePanel currentTime={currentTime} onSeek={handleSeek} />}
          {activeTab === "nodes" && <NodePanel onSeek={handleSeek} replayInteraction={replayInteraction} />}
          {activeTab === "chapters" && <ChapterPanel onSeek={handleSeek} />}
          {activeTab === "metadata" && <MetadataPanel />}
          {activeTab === "review" && (
            <div className="tab-panel">
              <pre>{JSON.stringify(artifacts.review, null, 2)}</pre>
            </div>
          )}
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

