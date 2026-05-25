import { useRef, useCallback } from "react";
import type { VideoItem, VideoArtifacts } from "../types";
import styles from "./VideoPlayer.module.css";

interface VideoPlayerProps {
  video: VideoItem;
  artifacts: VideoArtifacts;
  onTimeUpdate: (time: number) => void;
  videoRef?: React.Ref<HTMLVideoElement>;
}

export function VideoPlayer({ video, artifacts, onTimeUpdate, videoRef }: VideoPlayerProps) {
  const internalRef = useRef<HTMLVideoElement | null>(null);
  const subtitleRef = useRef<HTMLSpanElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLVideoElement | null) => {
      internalRef.current = node;
      if (typeof videoRef === "function") {
        videoRef(node);
      } else if (videoRef) {
        (videoRef as React.MutableRefObject<HTMLVideoElement | null>).current = node;
      }
    },
    [videoRef]
  );

  const handleTimeUpdate = useCallback(() => {
    const player = internalRef.current;
    if (!player) return;
    const time = player.currentTime;
    onTimeUpdate(time);

    // Update subtitle text directly via ref to avoid React re-render on every frame
    if (subtitleRef.current) {
      const subtitle = artifacts.subtitles.find((s) => time >= s.start && time < s.end);
      subtitleRef.current.textContent = subtitle?.text ?? "";
    }
  }, [onTimeUpdate, artifacts.subtitles]);

  const videoUrl = video.storage_dir
    ? `/api/videos/${video.id}/video`
    : "";

  return (
    <div className={styles.playerWrap}>
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
      <div className={styles.subtitleOverlay}>
        <span ref={subtitleRef} className={styles.subtitleText} />
      </div>
    </div>
  );
}
