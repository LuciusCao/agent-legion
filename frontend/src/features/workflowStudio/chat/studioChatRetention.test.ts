/** #1041：归档倒计时现算（归档时间 + 保留天数 − 现在，向上取整、下限 0）。 */
import { describe, expect, it } from 'vitest'
import {
  retentionCountdownLabel,
  retentionDaysLeft,
  retentionNotice,
} from './studioChatRetention'

const NOW = Date.parse('2026-03-31T12:00:00Z')

describe('studioChatRetention', () => {
  it('no window configured or no stamp: no countdown at all', () => {
    expect(retentionDaysLeft('2026-03-30T12:00:00Z', 0, NOW)).toBeNull()
    expect(retentionDaysLeft(null, 30, NOW)).toBeNull()
    expect(retentionDaysLeft(undefined, 30, NOW)).toBeNull()
    expect(retentionDaysLeft('not-a-date', 30, NOW)).toBeNull()
    expect(retentionNotice(0)).toBe('')
  })

  it('counts whole days left, rounding up, floored at 0', () => {
    expect(retentionDaysLeft('2026-03-31T12:00:00Z', 30, NOW)).toBe(30)
    expect(retentionDaysLeft('2026-03-29T12:00:00Z', 30, NOW)).toBe(28)
    expect(retentionDaysLeft('2026-03-02T00:00:00Z', 30, NOW)).toBe(1)
    expect(retentionDaysLeft('2026-02-01T00:00:00Z', 30, NOW)).toBe(0)
  })

  it('labels the countdown and the operation notice', () => {
    expect(retentionCountdownLabel(3)).toBe('3 天后清理')
    expect(retentionCountdownLabel(0)).toBe('即将清理')
    expect(retentionNotice(30)).toBe('将于 30 天后自动清理')
  })
})
