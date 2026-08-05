import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SubtitlePanel } from './SubtitlePanel'
import type { VideoArtifacts } from '../types'

const emptyArtifacts: VideoArtifacts = {
  subtitles: [],
  chapters: [],
  interactions: [],
  metadata: null,
  review: null,
  checklist: null,
}

describe('SubtitlePanel', () => {
  it('renders subtitle list', () => {
    render(
      <SubtitlePanel
        currentTime={0}
        onSeek={() => {}}
        artifacts={emptyArtifacts}
      />
    )
    expect(screen.getByRole('list')).toBeInTheDocument()
  })
})
