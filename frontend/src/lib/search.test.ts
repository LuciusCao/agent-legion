import { describe, it, expect } from 'vitest'
import { binarySearchTriggerIndex } from './search'
import type { InteractionNode } from '../types'

describe('binarySearchTriggerIndex', () => {
  it('returns -1 for empty array', () => {
    expect(binarySearchTriggerIndex(5, [])).toBe(-1)
  })

  it('returns exact match index', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 20 },
      { trigger_time: 30 },
    ]
    expect(binarySearchTriggerIndex(20, interactions)).toBe(1)
  })

  it('returns lower index when time is between triggers', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 20 },
      { trigger_time: 30 },
    ]
    expect(binarySearchTriggerIndex(25, interactions)).toBe(1)
  })

  it('returns -1 when time is less than first trigger', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 20 },
    ]
    expect(binarySearchTriggerIndex(5, interactions)).toBe(-1)
  })

  it('returns last index when time is greater than all triggers', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 20 },
      { trigger_time: 30 },
    ]
    expect(binarySearchTriggerIndex(50, interactions)).toBe(2)
  })

  it('parses string trigger_time correctly', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: '00:00:10' },
      { trigger_time: '00:01:30' },
      { trigger_time: '00:02:00' },
    ]
    expect(binarySearchTriggerIndex(60, interactions)).toBe(0)
    expect(binarySearchTriggerIndex(90, interactions)).toBe(1)
    expect(binarySearchTriggerIndex(150, interactions)).toBe(2)
  })

  it('skips invalid trigger_time entries', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 'invalid' },
      { trigger_time: 30 },
    ]
    expect(binarySearchTriggerIndex(5, interactions)).toBe(-1)
    expect(binarySearchTriggerIndex(20, interactions)).toBe(0)
    expect(binarySearchTriggerIndex(40, interactions)).toBe(2)
  })

  it('skips invalid trigger_time at edges', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 'bad' },
      { trigger_time: 10 },
      { trigger_time: 20 },
      { trigger_time: null as unknown as string },
    ]
    expect(binarySearchTriggerIndex(15, interactions)).toBe(1)
    expect(binarySearchTriggerIndex(25, interactions)).toBe(2)
  })
})
