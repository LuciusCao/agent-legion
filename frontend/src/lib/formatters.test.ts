import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  formatDateTime,
  formatInteractionStats,
  formatRelativeTime,
} from './formatters'

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2024-06-01T12:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns 刚刚 for less than 60 seconds', () => {
    expect(formatRelativeTime('2024-06-01T11:59:30.000Z')).toBe('刚刚')
  })

  it('returns minutes ago for less than an hour', () => {
    expect(formatRelativeTime('2024-06-01T11:58:00.000Z')).toBe('2 分钟前')
    expect(formatRelativeTime('2024-06-01T11:30:00.000Z')).toBe('30 分钟前')
  })

  it('returns hours ago for less than a day', () => {
    expect(formatRelativeTime('2024-06-01T10:00:00.000Z')).toBe('2 小时前')
  })

  it('returns days ago for less than 30 days', () => {
    expect(formatRelativeTime('2024-05-28T12:00:00.000Z')).toBe('4 天前')
  })

  it('returns locale date for 30 days or more', () => {
    expect(formatRelativeTime('2024-04-01T12:00:00.000Z')).toBe(
      new Date('2024-04-01T12:00:00.000Z').toLocaleDateString('zh-CN')
    )
  })
})

describe('formatDateTime', () => {
  it('renders offset-bearing ISO strings in the local timezone', () => {
    expect(formatDateTime('2026-07-22T02:15:31+00:00')).toBe(
      new Date('2026-07-22T02:15:31+00:00').toLocaleString('zh-CN')
    )
  })

  it('treats offset-less legacy strings as UTC', () => {
    expect(formatDateTime('2026-07-22 02:15:31')).toBe(
      new Date('2026-07-22T02:15:31Z').toLocaleString('zh-CN')
    )
  })

  it('returns em dash for missing values', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDateTime('')).toBe('—')
  })

  it('returns the raw value when it cannot be parsed', () => {
    expect(formatDateTime('not a date')).toBe('not a date')
  })
})

describe('formatInteractionStats', () => {
  it('returns empty string for undefined stats', () => {
    expect(formatInteractionStats(undefined)).toBe('')
  })

  it('returns empty string for empty stats', () => {
    expect(formatInteractionStats({})).toBe('')
  })

  it('formats single type stats', () => {
    expect(
      formatInteractionStats({ example_practice: { passed: 2, total: 3 } })
    ).toBe('例题试做 2/3')
  })

  it('formats multiple types in order', () => {
    expect(
      formatInteractionStats({
        example_practice: { passed: 2, total: 3 },
        interaction_summary: { passed: 1, total: 1 },
      })
    ).toBe('例题试做 2/3 ｜ 互动小结 1/1')
  })

  it('puts unknown types after known types', () => {
    expect(
      formatInteractionStats({
        unknown_type: { passed: 1, total: 2 },
        example_practice: { passed: 2, total: 3 },
      })
    ).toBe('例题试做 2/3 ｜ unknown_type 1/2')
  })

  it('treats video_summary same as interaction_summary in order', () => {
    expect(
      formatInteractionStats({
        video_summary: { passed: 1, total: 1 },
        example_practice: { passed: 2, total: 3 },
      })
    ).toBe('例题试做 2/3 ｜ 互动小结 1/1')
  })
})
