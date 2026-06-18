import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { formatRelativeTime } from './formatters'

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
