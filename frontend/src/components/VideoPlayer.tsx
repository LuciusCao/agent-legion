import { useRef, useCallback } from "react";
import type { VideoItem, VideoArtifacts } from "../types";

interface VideoPlayerProps {
  video: VideoItem;
  artifacts: VideoArtifacts;
  onTimeUpdate: (time: number) => void;
}

export function VideoPlayer({ video, artifacts, onTimeUpdate }: VideoPlayerProps) {
  const playerRef = useRef<HTMLVideoElement>(null);

  const handleTimeUpdate = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;
    onTimeUpdate(player.currentTime);
  }, [onTimeUpdate]);

  const videoUrl = video.storage_dir
    ? `/api/videos/${video.id}/stream`
    : "";

  return (
    <div className="player-wrap">
      {videoUrl ? (
        <video
          ref={playerRef}
          id="player"
          src={videoUrl}
          controls
          onTimeUpdate={handleTimeUpdate}
        />
      ) : (
        <div className="empty-state">视频文件未下载</div>
      )}
      <div className="subtitle-overlay">
        <span id="subtitleOverlay" className="subtitle-text" />
      </div>
    </div>
  );
}
