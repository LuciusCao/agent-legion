import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDetailStore } from "../stores/detailStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoStore } from "../stores/videoStore";
import { VideoPlayer } from "../components/VideoPlayer";
import { ChapterStrip } from "../components/ChapterStrip";
import { InteractionOverlay } from "../components/InteractionOverlay";
import { DetailTabs } from "../components/DetailTabs";
import { SubtitlePanel } from "../components/SubtitlePanel";
import { ChapterPanel } from "../components/ChapterPanel";
import { NodePanel } from "../components/NodePanel";
import { MetadataPanel } from "../components/MetadataPanel";
import { RerunDialog } from "../components/RerunDialog";
import { TYPE_LABELS, PHASE_LABELS, STATUS_LABELS } from "../labels";
import { statusGroup } from "../helpers";
import { PhaseStepper } from "../components/PhaseStepper";
import { api } from "../api";

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const playerRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);

  const {
    currentVideo,
    artifacts,
    log,
    activeTab,
    triggeredNodeIndexes,
    dismissedNodeIndexes,
    currentSentence,
    loadVideo,
    loadArtifacts,
    loadLog,
    triggerInteraction,
    dismissInteraction,
    pushWord,
    resetSentence,
    clearSentence,
  } = useDetailStore();

  const { openRerunDialog, showToast } = useUiStore();
  const { fetchVideos } = useVideoStore();

  useEffect(() => {
    if (!id) return;
    loadVideo(id);
    loadArtifacts(id);
    loadLog(id);
  }, [id, loadVideo, loadArtifacts, loadLog]);

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

  const handleDelete = useCallback(async () => {
    if (!id || !window.confirm("确定删除该资源？本地视频和处理产物目录也会删除。")) return;
    await api(`/api/videos/${id}`, { method: "DELETE" });
    showToast("删除成功", "success");
    await fetchVideos();
    navigate("/");
  }, [id, navigate, fetchVideos, showToast]);

  const handlePackage = useCallback(async () => {
    if (!id) return;
    const result = await api<{ download_url: string }>("/api/package", {
      method: "POST",
      body: JSON.stringify({ video_ids: [id] }),
    });
    window.location.href = result.download_url;
  }, [id]);

  const handleRerun = useCallback(
    async (phase: string) => {
      if (!id) return;
      try {
        await api(`/api/videos/${id}/rerun`, { method: "POST", body: JSON.stringify({ phase }) });
        showToast("重跑已提交", "success");
        await fetchVideos();
      } catch (err) {
        if (err instanceof Error && err.message.includes("currently being processed")) {
          showToast("该资源正在被处理中，请等待当前阶段完成后再重跑。", "error");
        } else {
          throw err;
        }
      }
    },
    [id, fetchVideos, showToast]
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
              {PHASE_LABELS[currentVideo.current_phase] || currentVideo.current_phase}
            </span>
            <PhaseStepper video={currentVideo} />
            <span className={`status-badge ${statusGroup(currentVideo)}`}>
              {STATUS_LABELS[statusGroup(currentVideo)] || currentVideo.status}
            </span>
          </div>
        )}
        <div className="detail-actions">
          <md-text-button onClick={openRerunDialog}>重跑</md-text-button>
          <md-text-button onClick={handlePackage}>打包</md-text-button>
          <md-text-button style={{ color: "var(--md-sys-color-error)" }} onClick={handleDelete}>
            删除
          </md-text-button>
        </div>
      </header>

      <section className="preview-layout">
        <div className="preview-main">
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
        <aside className="play-info-panel">
          <div className="info-row">
            <span>当前时间</span>
            <p>{formatTime(currentTime)}</p>
          </div>
          <div className="info-row">
            <span>总时长</span>
            <p>{currentVideo?.duration ? formatTime(currentVideo.duration) : "—"}</p>
          </div>
          <div className="info-row">
            <span>当前章节</span>
            <p>
              {artifacts.chapters.find((c, i, arr) => currentTime >= c.start && currentTime < (arr[i + 1]?.start ?? Infinity))?.title || "—"}
            </p>
          </div>
          <div className="info-row">
            <span>日志</span>
            <pre style={{ maxHeight: "200px", overflow: "auto" }}>{log}</pre>
          </div>
        </aside>
      </section>

      <section className="detail-bottom">
        {currentVideo && <DetailTabs contentType={currentVideo.content_type} />}
        <div className="tab-panel">
          {activeTab === "subtitles" && <SubtitlePanel currentTime={currentTime} onSeek={handleSeek} />}
          {activeTab === "nodes" && <NodePanel onSeek={handleSeek} />}
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
    </section>
  );
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
