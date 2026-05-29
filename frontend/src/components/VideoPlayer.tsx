import { useRef, useCallback } from 'react'
import type { InteractionNode, VideoItem, VideoArtifacts } from '../types'
import { InteractionOverlay } from './InteractionOverlay'
import styles from './VideoPlayer.module.css'

interface VideoPlayerProps {
  video: VideoItem
  artifacts: VideoArtifacts
  onTimeUpdate: (time: number) => void
  videoRef?: React.RefObject<HTMLVideoElement | null>
  interactionNode?: InteractionNode | null
  interactionSentence?: string[]
  onInteractionWordClick?: (word: string) => void
  onInteractionReset?: () => void
  onInteractionContinue?: () => void
}

export function VideoPlayer({
  video,
  artifacts,
  onTimeUpdate,
  videoRef,
  interactionNode = null,
  interactionSentence = [],
  onInteractionWordClick = () => {},
  onInteractionReset = () => {},
  onInteractionContinue = () => {},
}: VideoPlayerProps) {
  const internalRef = useRef<HTMLVideoElement | null>(null)
  const subtitleRef = useRef<HTMLSpanElement | null>(null)

  const setRefs = useCallback(
    // eslint-disable-next-line react-hooks/immutability
    (node: HTMLVideoElement | null) => {
      internalRef.current = node
      if (videoRef) {
        // eslint-disable-next-line react-hooks/immutability
        ;(videoRef as React.MutableRefObject<HTMLVideoElement | null>).current =
          node
      }
    },
    [videoRef]
  )

  const handleTimeUpdate = useCallback(() => {
    const player = internalRef.current
    if (!player) return
    const time = player.currentTime
    onTimeUpdate(time)

    // Update subtitle text directly via ref to avoid React re-render on every frame
    if (subtitleRef.current) {
      const subtitle = artifacts.subtitles.find(
        (s) => time >= s.start && time < s.end
      )
      subtitleRef.current.textContent = subtitle?.text ?? ''
    }
  }, [onTimeUpdate, artifacts.subtitles])

  const videoUrl = video.storage_dir ? `/api/videos/${video.id}/video` : ''

  return (
    <div className={styles.playerWrap} data-testid="video-player-wrap">
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
      <InteractionOverlay
        node={interactionNode}
        currentSentence={interactionSentence}
        onWordClick={onInteractionWordClick}
        onReset={onInteractionReset}
        onContinue={onInteractionContinue}
      />
      <div className={styles.subtitleOverlay}>
        <span ref={subtitleRef} className={styles.subtitleText} />
      </div>
    </div>
  )
}
