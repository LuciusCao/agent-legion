import { describe, it, expect, beforeEach } from 'vitest'
import { useVideoNodeStore } from './videoNodeStore'

describe('interactionStore', () => {
  beforeEach(() => {
    useVideoNodeStore.setState({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
    })
  })

  it('triggers interaction', () => {
    useVideoNodeStore.getState().triggerInteraction(0)
    expect(useVideoNodeStore.getState().triggeredNodeIndexes.has(0)).toBe(true)
  })

  it('dismisses interaction', () => {
    useVideoNodeStore.getState().triggerInteraction(0)
    useVideoNodeStore.getState().dismissInteraction(0)
    expect(useVideoNodeStore.getState().triggeredNodeIndexes.has(0)).toBe(false)
    expect(useVideoNodeStore.getState().dismissedNodeIndexes.has(0)).toBe(true)
  })

  it('replayInteraction resets dismissed and re-triggers node', () => {
    useVideoNodeStore.getState().triggerInteraction(0)
    useVideoNodeStore.getState().dismissInteraction(0)
    expect(useVideoNodeStore.getState().triggeredNodeIndexes.has(0)).toBe(false)
    expect(useVideoNodeStore.getState().dismissedNodeIndexes.has(0)).toBe(true)

    useVideoNodeStore.getState().replayInteraction(0)
    expect(useVideoNodeStore.getState().triggeredNodeIndexes.has(0)).toBe(true)
    expect(useVideoNodeStore.getState().dismissedNodeIndexes.has(0)).toBe(false)
    expect(useVideoNodeStore.getState().currentSentence).toEqual([])
  })

  it('pushWord adds to currentSentence', () => {
    useVideoNodeStore.getState().pushWord('hello')
    useVideoNodeStore.getState().pushWord('world')
    expect(useVideoNodeStore.getState().currentSentence).toEqual([
      'hello',
      'world',
    ])
  })

  it('clearSentence resets currentSentence', () => {
    useVideoNodeStore.setState({ currentSentence: ['hello'] })
    useVideoNodeStore.getState().clearSentence()
    expect(useVideoNodeStore.getState().currentSentence).toEqual([])
  })

  it('clearInteractions resets trigger, dismiss, and sentence state', () => {
    useVideoNodeStore.setState({
      triggeredNodeIndexes: new Set([0]),
      dismissedNodeIndexes: new Set([1]),
      currentSentence: ['hello'],
    })

    useVideoNodeStore.getState().clearInteractions()

    expect(useVideoNodeStore.getState().triggeredNodeIndexes.size).toBe(0)
    expect(useVideoNodeStore.getState().dismissedNodeIndexes.size).toBe(0)
    expect(useVideoNodeStore.getState().currentSentence).toEqual([])
  })
})
