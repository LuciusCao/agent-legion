import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NodePanel } from './NodePanel'
import { useVideoNodeStore } from '../stores/videoNodeStore'
import type { VideoArtifacts } from '../types'
import styles from './NodePanel.module.css'

function makeArtifacts(
  overrides: Partial<VideoArtifacts> = {}
): VideoArtifacts {
  return {
    subtitles: [],
    chapters: [],
    interactions: [
      {
        id: 'n1',
        trigger_time: 90,
        instruction: 'Test instruction',
        type: 'example_practice',
        options: [{ id: 'a', text: 'Option A', is_distractor: false }],
      },
    ],
    metadata: null,
    review: null,
    checklist: null,
    ...overrides,
  }
}

describe('NodePanel', () => {
  beforeEach(() => {
    useVideoNodeStore.setState({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
    })
  })

  it('clicking node card calls onSeek and replayInteraction', () => {
    const onSeek = vi.fn()
    const replayInteraction = vi.fn()

    render(
      <NodePanel
        artifacts={makeArtifacts()}
        onSeek={onSeek}
        replayInteraction={replayInteraction}
      />
    )
    const card = screen
      .getByText('Test instruction')
      .closest(`.${styles.nodeCard}`)
    fireEvent.click(card!)
    expect(onSeek).toHaveBeenCalledWith(90)
    expect(replayInteraction).toHaveBeenCalledWith(0)
  })

  it('shows review status badge when review has matching item_id', () => {
    const artifacts = makeArtifacts({
      interactions: [
        { id: 'n1', trigger_time: 10, instruction: 'Node A', type: 'quiz' },
        { id: 'n2', trigger_time: 20, instruction: 'Node B', type: 'quiz' },
      ],
      review: {
        score: 85,
        status: 'pending_review',
        reviews: [
          { item_id: 'n1', status: 'published', issues: [] },
          {
            item_id: 'n2',
            status: 'rejected',
            issues: [{ title: '选项错误', details: 'B选项应为干扰项' }],
          },
        ],
      },
    })

    render(<NodePanel artifacts={artifacts} />)
    expect(screen.getByText('已通过')).toBeInTheDocument()
    expect(screen.getByText('驳回')).toBeInTheDocument()
    expect(screen.getByText('选项错误：B选项应为干扰项')).toBeInTheDocument()
  })

  it('falls back to global status when no per-node review exists', () => {
    const artifacts = makeArtifacts({
      interactions: [
        { id: 'n1', trigger_time: 10, instruction: 'Node A', type: 'quiz' },
      ],
      review: { status: 'published' },
    })

    render(<NodePanel artifacts={artifacts} />)
    expect(screen.getByText('已通过')).toBeInTheDocument()
  })

  it('hides review info when review is null', () => {
    render(<NodePanel artifacts={makeArtifacts()} />)
    expect(screen.queryByText('已通过')).not.toBeInTheDocument()
    expect(screen.queryByText('待审')).not.toBeInTheDocument()
    expect(screen.queryByText('驳回')).not.toBeInTheDocument()
  })
})
