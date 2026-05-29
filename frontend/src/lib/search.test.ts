import { describe, it, expect } from 'vitest'
import { binarySearchTriggerIndex, prepareIndexedTriggers } from './search'
import type { InteractionNode } from '../types'

describe('prepareIndexedTriggers', () => {
  it('returns empty array for empty input', () => {
    expect(prepareIndexedTriggers([])).toEqual([])
  })

  it('filters out invalid trigger_time entries', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 'invalid' },
      { trigger_time: 30 },
    ]
    const result = prepareIndexedTriggers(interactions)
    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({ index: 0, trigger: 10 })
    expect(result[1]).toEqual({ index: 2, trigger: 30 })
  })

  it('parses string trigger_time correctly', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: '00:00:10' },
      { trigger_time: '00:01:30' },
    ]
    const result = prepareIndexedTriggers(interactions)
    expect(result).toEqual([
      { index: 0, trigger: 10 },
      { index: 1, trigger: 90 },
    ])
  })

  it('skips invalid entries at edges', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 'bad' },
      { trigger_time: 10 },
      { trigger_time: 20 },
      { trigger_time: null as unknown as string },
    ]
    const result = prepareIndexedTriggers(interactions)
    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({ index: 1, trigger: 10 })
    expect(result[1]).toEqual({ index: 2, trigger: 20 })
  })
})

describe('binarySearchTriggerIndex', () => {
  it('returns -1 for empty array', () => {
    expect(binarySearchTriggerIndex(5, [])).toBe(-1)
  })

  it('returns exact match index', () => {
    const indexed = [
      { index: 0, trigger: 10 },
      { index: 1, trigger: 20 },
      { index: 2, trigger: 30 },
    ]
    expect(binarySearchTriggerIndex(20, indexed)).toBe(1)
  })

  it('returns lower index when time is between triggers', () => {
    const indexed = [
      { index: 0, trigger: 10 },
      { index: 1, trigger: 20 },
      { index: 2, trigger: 30 },
    ]
    expect(binarySearchTriggerIndex(25, indexed)).toBe(1)
  })

  it('returns -1 when time is less than first trigger', () => {
    const indexed = [
      { index: 0, trigger: 10 },
      { index: 1, trigger: 20 },
    ]
    expect(binarySearchTriggerIndex(5, indexed)).toBe(-1)
  })

  it('returns last index when time is greater than all triggers', () => {
    const indexed = [
      { index: 0, trigger: 10 },
      { index: 1, trigger: 20 },
      { index: 2, trigger: 30 },
    ]
    expect(binarySearchTriggerIndex(50, indexed)).toBe(2)
  })

  it('works with prepared string trigger_time', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: '00:00:10' },
      { trigger_time: '00:01:30' },
      { trigger_time: '00:02:00' },
    ]
    const indexed = prepareIndexedTriggers(interactions)
    expect(binarySearchTriggerIndex(60, indexed)).toBe(0)
    expect(binarySearchTriggerIndex(90, indexed)).toBe(1)
    expect(binarySearchTriggerIndex(150, indexed)).toBe(2)
  })

  it('skips invalid trigger_time entries via prepareIndexedTriggers', () => {
    const interactions: InteractionNode[] = [
      { trigger_time: 10 },
      { trigger_time: 'invalid' },
      { trigger_time: 30 },
    ]
    const indexed = prepareIndexedTriggers(interactions)
    expect(binarySearchTriggerIndex(5, indexed)).toBe(-1)
    expect(binarySearchTriggerIndex(20, indexed)).toBe(0)
    expect(binarySearchTriggerIndex(40, indexed)).toBe(2)
  })
})
