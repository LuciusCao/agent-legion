import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { VideoPlayer } from './VideoPlayer'
import type { VideoArtifacts, VideoItem } from '../types'

const video: VideoItem = {
  id: 'v1',
  title: 'Test',
  content_type: 'knowledge',
  status: 'queued',
  source_url: '',
  external_id: '',
  knowledge_code: '',
  question_id: '',
  source_uuid: '',
  current_phase: 'download',
  error_message: '',
  storage_dir: '',
  duration: 0,
  packed: false,
}

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
    render(
      <VideoPlayer video={video} artifacts={artifacts} onTimeUpdate={vi.fn()} />
    )
    expect(screen.getByText('视频文件未下载')).toBeInTheDocument()
  })

  it('renders an active interaction inside the player wrapper', () => {
    render(
      <VideoPlayer
        video={{ ...video, storage_dir: '/tmp/video' }}
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
})
