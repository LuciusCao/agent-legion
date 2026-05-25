import { useState } from "react";
import { useVideoStore } from "../stores/videoStore";
import { getPhases, canRerunFrom } from "../helpers";
import type { VideoItem } from "../types";

const PHASE_LABELS: Record<string, string> = {
  download: "下载",
  transcribe: "转录",
  subtitle_review: "字幕审校",
  chapter_generate: "章节生成",
  interaction_generate: "互动生成",
  content_review: "内容审校",
  assemble: "组装",
  package: "打包",
};

type BatchRerunDialogProps = {
  open: boolean;
  videoIds: string[];
  onClose: () => void;
};

export function BatchRerunDialog({ open, videoIds, onClose }: BatchRerunDialogProps) {
  const { videos, batchRerun, clearSelection, fetchVideos } = useVideoStore();
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
    clearSelection();
    await fetchVideos();
  };

  return (
    <md-dialog
      open
      onClosed={onClose}
      style={
        {
          minWidth: "520px",
          "--md-dialog-container-color": "#ffffff",
        } as React.CSSProperties
      }
    >
      <div slot="headline">选择重跑阶段</div>
      <div slot="content">
        <div style={{ display: "grid", gap: "16px", minWidth: "460px" }}>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {phases.map((phase) => (
              <md-filter-chip
                key={phase}
                label={PHASE_LABELS[phase] ?? phase}
                selected={selectedPhase === phase || undefined}
                onClick={() => setSelectedPhase(phase)}
              />
            ))}
          </div>
          <div
            style={{
              display: "grid",
              gap: "8px",
              maxHeight: "240px",
              overflowY: "auto",
            }}
          >
            {selectedVideos.map((video) => {
              const runnable = canRerunFrom(video, selectedPhase);
              return (
                <div
                  key={video.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "8px 12px",
                    borderRadius: "8px",
                    background: "var(--md-sys-color-surface-container-low)",
                    opacity: runnable ? 1 : 0.5,
                  }}
                >
                  <span>{displayName(video)}</span>
                  {!runnable && (
                    <span style={{ color: "var(--md-sys-color-error)", fontSize: "12px" }}>
                      当前处于 {PHASE_LABELS[video.current_phase] ?? video.current_phase}
                      ，无法重跑
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: "14px", color: "var(--md-sys-color-on-surface-variant)" }}>
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
