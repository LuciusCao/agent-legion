import { describe, it, expect, beforeEach } from 'vitest'
import { useArtifactStore } from './artifactStore'

describe('artifactStore', () => {
  beforeEach(() => {
    useArtifactStore.setState({
      artifacts: {
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      },
    })
  })

  it('resets artifacts', () => {
    useArtifactStore.setState({
      artifacts: {
        subtitles: [{ index: 1, start: 1, end: 3, text: 'hello' }],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      },
    })
    useArtifactStore.getState().resetArtifacts()
    expect(useArtifactStore.getState().artifacts.subtitles).toHaveLength(0)
  })
})
