import { describe, expect, it } from 'vitest'
import type { AgentWorkerSummary } from '../api/agentWorkers'
import { needsConsoleUrl, workersMeetingNeeds } from './workerNeedsFleet'

describe('workersMeetingNeeds', () => {
  const agentOnly = { worker_id: 'a', max_code_concurrency: 0 }
  const codeCapable = { worker_id: 'c', max_code_concurrency: 2 }

  it('keeps every Worker for agent-only needs', () => {
    expect(
      workersMeetingNeeds([agentOnly, codeCapable], {
        agent: true,
        code: false,
      })
    ).toEqual([agentOnly, codeCapable])
  })

  it('keeps only code-capable Workers when code nodes need a Worker', () => {
    expect(
      workersMeetingNeeds([agentOnly, codeCapable], {
        agent: true,
        code: true,
      })
    ).toEqual([codeCapable])
  })
})

describe('needsConsoleUrl', () => {
  function worker(url: string, maxCode: number) {
    return {
      online: true,
      revoked: false,
      max_code_concurrency: maxCode,
      labels: { console_url: url },
    } as unknown as AgentWorkerSummary
  }
  const codeNeeds = { agent: false, code: true }

  it('prefers a capable Worker console when one is online', () => {
    expect(
      needsConsoleUrl(
        [worker('http://agent/', 0), worker('http://code/', 1)],
        codeNeeds,
        'http://fallback/'
      )
    ).toBe('http://code/')
  })

  it('points at the Worker that needs code concurrency when none is capable', () => {
    expect(needsConsoleUrl([worker('http://agent/', 0)], codeNeeds, '')).toBe(
      'http://agent/'
    )
  })

  it('falls back to the deployment console without self-reported addresses', () => {
    expect(needsConsoleUrl([], codeNeeds, 'http://fallback/')).toBe(
      'http://fallback/'
    )
  })
})
