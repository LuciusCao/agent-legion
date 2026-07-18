import { describe, it, expect, vi } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import { VideoPlayer } from './VideoPlayer'
import type { VideoArtifacts } from '../types'

const artifacts: VideoArtifacts = {
  subtitles: [],
  chapters: [],
  interactions: [],
  metadata: null,
  review: null,
  checklist: null,
}

describe('VideoPlayer', () => {
  it('renders empty state when no video URL', () => {
    render(<VideoPlayer artifacts={artifacts} onTimeUpdate={vi.fn()} />)
    expect(screen.getByText('视频文件未下载')).toBeInTheDocument()
  })

  it('renders an active interaction inside the player wrapper', () => {
    render(
      <VideoPlayer
        src="/api/jobs/video-v1/video/source"
        artifacts={artifacts}
        onTimeUpdate={vi.fn()}
        interactionNode={{
          type: 'example_practice',
          instruction: '先暂停完成这道例题',
        }}
        interactionSentence={[]}
        onInteractionWordClick={vi.fn()}
        onInteractionReset={vi.fn()}
        onInteractionContinue={vi.fn()}
      />
    )

    const playerWrap = screen.getByTestId('video-player-wrap')
    expect(
      within(playerWrap).getByText('先暂停完成这道例题')
    ).toBeInTheDocument()
  })

  it('updates subtitle text via state on timeupdate', () => {
    const subtitleArtifacts: VideoArtifacts = {
      ...artifacts,
      subtitles: [
        { index: 1, start: 0, end: 5, text: 'First subtitle' },
        { index: 2, start: 5, end: 10, text: 'Second subtitle' },
      ],
    }

    render(
      <VideoPlayer
        src="/api/jobs/video-v1/video/source"
        artifacts={subtitleArtifacts}
        onTimeUpdate={vi.fn()}
      />
    )

    const videoEl = document.getElementById('player') as HTMLVideoElement

    videoEl.currentTime = 3
    fireEvent.timeUpdate(videoEl)
    expect(screen.getByText('First subtitle')).toBeInTheDocument()

    videoEl.currentTime = 7
    fireEvent.timeUpdate(videoEl)
    expect(screen.getByText('Second subtitle')).toBeInTheDocument()
  })

  it('calls onPlay and onPause callbacks', () => {
    const onPlay = vi.fn()
    const onPause = vi.fn()

    render(
      <VideoPlayer
        src="/api/jobs/video-v1/video/source"
        artifacts={artifacts}
        onTimeUpdate={vi.fn()}
        onPlay={onPlay}
        onPause={onPause}
      />
    )

    const videoEl = document.getElementById('player') as HTMLVideoElement

    fireEvent.play(videoEl)
    expect(onPlay).toHaveBeenCalledTimes(1)

    fireEvent.pause(videoEl)
    expect(onPause).toHaveBeenCalledTimes(1)
  })
})
