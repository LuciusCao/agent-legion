import { useRef, useCallback, useState } from 'react'
import type { InteractionNode, VideoArtifacts } from '../types'
import { InteractionOverlay } from './InteractionOverlay'
import { RichText } from './RichText'
import styles from './VideoPlayer.module.css'

interface VideoPlayerProps {
  artifacts: VideoArtifacts
  onTimeUpdate: (time: number) => void
  videoRef?: React.RefObject<HTMLVideoElement | null>
  src?: string
  interactionNode?: InteractionNode | null
  interactionSentence?: string[]
  onInteractionWordClick?: (word: string) => void
  onInteractionReset?: () => void
  onInteractionContinue?: () => void
  onPlay?: () => void
  onPause?: () => void
}

function findSubtitleIndex(
  subtitles: Array<{ start: number; end: number }>,
  time: number
): number {
  let left = 0
  let right = subtitles.length - 1
  while (left <= right) {
    const mid = Math.floor((left + right) / 2)
    const s = subtitles[mid]
    if (time >= s.start && time < s.end) return mid
    if (time < s.start) right = mid - 1
    else left = mid + 1
  }
  return -1
}

export function VideoPlayer({
  artifacts,
  onTimeUpdate,
  videoRef,
  src,
  interactionNode = null,
  interactionSentence = [],
  onInteractionWordClick = () => {},
  onInteractionReset = () => {},
  onInteractionContinue = () => {},
  onPlay = () => {},
  onPause = () => {},
}: VideoPlayerProps) {
  const internalRef = useRef<HTMLVideoElement | null>(null)
  const [subtitleText, setSubtitleText] = useState('')

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

    const idx = findSubtitleIndex(artifacts.subtitles, time)
    setSubtitleText(artifacts.subtitles[idx]?.text ?? '')
  }, [onTimeUpdate, artifacts.subtitles])

  const videoUrl = src || ''

  return (
    <div className={styles.playerWrap} data-testid="video-player-wrap">
      {videoUrl ? (
        <video
          ref={setRefs}
          id="player"
          src={videoUrl}
          controls
          onTimeUpdate={handleTimeUpdate}
          onPlay={onPlay}
          onPause={onPause}
        />
      ) : (
        <div className="empty-state">视频文件未下载</div>
      )}
      <InteractionOverlay
        key={interactionNode?.id ?? 'none'}
        node={interactionNode}
        currentSentence={interactionSentence}
        onWordClick={onInteractionWordClick}
        onReset={onInteractionReset}
        onContinue={onInteractionContinue}
      />
      <div className={styles.subtitleOverlay}>
        <span className={styles.subtitleText}>
          <RichText mode="inline">{subtitleText}</RichText>
        </span>
      </div>
    </div>
  )
}
