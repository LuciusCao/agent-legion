import { useRef, useCallback } from "react";
import type { VideoItem, VideoArtifacts } from "../types";

interface VideoPlayerProps {
  video: VideoItem;
  artifacts: VideoArtifacts;
  onTimeUpdate: (time: number) => void;
  videoRef?: React.Ref<HTMLVideoElement>;
}

export function VideoPlayer({ video, artifacts: _artifacts, onTimeUpdate, videoRef }: VideoPlayerProps) {
  const internalRef = useRef<HTMLVideoElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLVideoElement | null) => {
      internalRef.current = node;
      if (typeof videoRef === "function") {
        videoRef(node);
      } else if (videoRef) {
        (videoRef as any).current = node;
      }
    },
    [videoRef]
  );

  const handleTimeUpdate = useCallback(() => {
    const player = internalRef.current;
    if (!player) return;
    onTimeUpdate(player.currentTime);
  }, [onTimeUpdate]);

  const videoUrl = video.storage_dir
    ? `/api/videos/${video.id}/video`
    : "";

  return (
    <div className="player-wrap">
      {videoUrl ? (
        <video
          ref={setRefs}
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
