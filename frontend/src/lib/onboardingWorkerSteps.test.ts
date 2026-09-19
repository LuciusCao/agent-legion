import { describe, expect, it, vi } from 'vitest'
import type { AgentWorkerSummary } from '../api/agentWorkers'
import {
  buildWorkerOnboardingSteps,
  withWorkerSteps,
} from './onboardingWorkerSteps'

function worker(
  overrides: Partial<AgentWorkerSummary> = {}
): AgentWorkerSummary {
  return {
    worker_id: 'w1',
    name: 'mac',
    runtimes: ['pi'],
    capabilities: [],
    models: [],
    max_concurrency: 1,
    max_code_concurrency: 0,
    labels: {},
    protocol_version: 1,
    allowed_workspaces: ['ws1'],
    register_token_ids: [],
    registered_at: '2026-09-01T00:00:00Z',
    last_seen_at: '2026-09-01T00:00:00Z',
    online: true,
    revoked: false,
    claim_enabled: null,
    ...overrides,
  }
}

function build(
  overrides: Partial<Parameters<typeof buildWorkerOnboardingSteps>[0]> = {}
) {
  return buildWorkerOnboardingSteps({
    workers: [],
    paused: true,
    consoleUrl: '',
    goWorkerSettings: vi.fn(),
    resumeScheduling: vi.fn(),
    openConsole: vi.fn(),
    ...overrides,
  })
}

describe('buildWorkerOnboardingSteps', () => {
  it('locks the switch step until a worker is online', () => {
    const [connect, switches] = build()
    expect(connect.completed).toBe(false)
    expect(connect.actionLabel).toBe('去接入 Worker')
    expect(switches.unlocked).toBe(false)
  })

  it('completes the connect step and offers to resume scheduling while paused', () => {
    const resume = vi.fn()
    const [connect, switches] = build({
      workers: [worker({ claim_enabled: false })],
      paused: true,
      resumeScheduling: resume,
    })
    expect(connect.completed).toBe(true)
    expect(switches.unlocked).toBe(true)
    expect(switches.completed).toBe(false)
    expect(switches.actionLabel).toBe('恢复调度')
    switches.onAction()
    expect(resume).toHaveBeenCalled()
  })

  it('points at the worker console (self-reported first) when only claiming is missing', () => {
    const open = vi.fn()
    const [, switches] = build({
      workers: [
        worker({
          claim_enabled: false,
          labels: { console_url: 'http://10.0.0.8:8787' },
        }),
      ],
      paused: false,
      consoleUrl: 'http://127.0.0.1:8789',
      openConsole: open,
    })
    expect(switches.completed).toBe(false)
    expect(switches.actionLabel).toBe('打开 Worker 控制台')
    switches.onAction()
    expect(open).toHaveBeenCalledWith('http://10.0.0.8:8787')
  })

  it('is complete once a worker claims and scheduling runs; legacy workers count as claiming', () => {
    const [, switches] = build({
      workers: [worker({ claim_enabled: null })],
      paused: false,
    })
    expect(switches.completed).toBe(true)
    const [, idle] = build({
      workers: [worker({ claim_enabled: false })],
      paused: false,
    })
    expect(idle.completed).toBe(false)
  })

  it('slots the worker steps between publish and add-items', () => {
    const core = [
      { title: 'publish' },
      { title: 'add' },
    ] as unknown as ReturnType<typeof build>
    const merged = withWorkerSteps(core, build())
    expect(merged.map((step) => step.title)).toEqual([
      'publish',
      '接入 Worker',
      '打开执行开关',
      'add',
    ])
  })
})
