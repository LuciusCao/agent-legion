import { describe, expect, it } from 'vitest'
import {
  hasClaimingWorker,
  hasOnlineWorker,
  presenceTitle,
  workerPresence,
} from './workerPresence'

describe('workerPresence', () => {
  it('maps online + claim switch into four states', () => {
    expect(workerPresence({ online: false, claim_enabled: true })).toBe(
      'offline'
    )
    expect(workerPresence({ online: true, claim_enabled: null })).toBe('online')
    expect(workerPresence({ online: true, claim_enabled: true })).toBe(
      'claiming'
    )
    expect(workerPresence({ online: true, claim_enabled: false })).toBe(
      'not_claiming'
    )
  })

  it('puts the fix first in the not-claiming title', () => {
    expect(presenceTitle('not_claiming', '最近心跳 x')).toContain('开始领取')
    expect(presenceTitle('not_claiming', '最近心跳 x')).toContain('最近心跳 x')
    expect(presenceTitle('claiming', '最近心跳 x')).toBe('最近心跳 x')
  })

  it('judges fleet readiness with legacy workers counted as claiming', () => {
    const offline = { online: false, claim_enabled: true, revoked: false }
    const idle = { online: true, claim_enabled: false, revoked: false }
    const legacy = { online: true, claim_enabled: null, revoked: false }
    const revoked = { online: true, claim_enabled: true, revoked: true }
    expect(hasOnlineWorker([offline, revoked])).toBe(false)
    expect(hasOnlineWorker([offline, idle])).toBe(true)
    expect(hasClaimingWorker([offline, idle])).toBe(false)
    expect(hasClaimingWorker([idle, legacy])).toBe(true)
  })
})
