import { useState } from "react";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";
import { getPhases, canRerunFrom } from "../helpers";
import { PHASE_LABELS } from "../labels";
import type { VideoItem } from "../types";
import styles from "./BatchRerunDialog.module.css";

type BatchRerunDialogProps = {
  open: boolean;
  videoIds: string[];
  onClose: () => void;
};

export function BatchRerunDialog({ open, videoIds, onClose }: BatchRerunDialogProps) {
  const { videos, batchRerun, exitSelectMode, fetchVideos } = useVideoStore();
  const { showToast } = useUiStore();
  const [selectedPhase, setSelectedPhase] = useState("download");

  if (!open) return null;

  const selectedVideos = videos.filter((v) => videoIds.includes(v.id));
  const contentType = selectedVideos[0]?.content_type ?? "knowledge";
  const phases = getPhases(contentType);

  const runnableCount = selectedVideos.filter((v) =>
    canRerunFrom(v, selectedPhase),
  ).length;

  const displayName = (video: VideoItem) =>
    video.external_id || video.title || video.id;

  const handleConfirm = async () => {
    await batchRerun(videoIds, selectedPhase);
    onClose();
    exitSelectMode();
    await fetchVideos();
    const err = useVideoStore.getState().error;
    if (err) {
      showToast(`加载失败: ${err}`, "error");
      useVideoStore.getState().clearError();
    }
  };

  return (
    <md-dialog
      open
      onClosed={onClose}
      style={
        {
          minWidth: "520px",
          maxWidth: "760px",
          width: "min(760px, 92vw)",
          "--md-dialog-container-color": "#ffffff",
        } as React.CSSProperties
      }
    >
      <div slot="headline">选择重跑阶段</div>
      <div slot="content">
        <div className={styles.content}>
          <div className={styles.phaseGrid}>
            {phases.map((phase) => (
              <md-filter-chip
                key={phase}
                label={PHASE_LABELS[phase] ?? phase}
                selected={selectedPhase === phase || undefined}
                onClick={() => setSelectedPhase(phase)}
              />
            ))}
          </div>
          <div className={styles.videoGrid}>
            {selectedVideos.map((video) => {
              const runnable = canRerunFrom(video, selectedPhase);
              return (
                <div
                  key={video.id}
                  className={`${styles.videoTile} ${runnable ? "" : styles.videoTileDisabled}`}
                >
                  <span className={styles.videoName}>{displayName(video)}</span>
                  {!runnable && (
                    <span className={styles.videoHint}>
                      当前处于 {PHASE_LABELS[video.current_phase] ?? video.current_phase}
                      ，无法重跑
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <div className={styles.summary}>
            已选择 {selectedVideos.length} 个视频，可重跑 {runnableCount} 个
          </div>
        </div>
      </div>
      <div slot="actions">
        <md-text-button type="button" onClick={onClose}>
          取消
        </md-text-button>
        <md-filled-button
          onClick={handleConfirm}
          disabled={runnableCount === 0 || undefined}
        >
          重跑 {runnableCount} 个视频
        </md-filled-button>
      </div>
    </md-dialog>
  );
}
