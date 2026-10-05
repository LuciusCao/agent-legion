import { describe, expect, it } from 'vitest'
import {
  ACTIVE_POLL_INTERVAL_MS,
  AWAITING_APPROVAL_POLL_INTERVAL_MS,
  jobDetailPollInterval,
} from './jobDetailPolling'
import type { JobDetail } from '../../types/jobTypes'

function detail(
  status: string,
  nodeStatuses: string[] = ['completed']
): Pick<JobDetail, 'job' | 'nodes'> {
  return {
    job: { id: 'j1', status } as JobDetail['job'],
    nodes: nodeStatuses.map(
      (nodeStatus, index) =>
        ({
          node_key: `n${index}`,
          status: nodeStatus,
        }) as JobDetail['nodes'][number]
    ),
  }
}

describe('jobDetailPollInterval (#965)', () => {
  it.each(['queued', 'running'])('活跃执行态 %s 保持 5s', (status) => {
    expect(jobDetailPollInterval(detail(status))).toBe(ACTIVE_POLL_INTERVAL_MS)
    expect(ACTIVE_POLL_INTERVAL_MS).toBe(5_000)
  })

  it('awaiting_approval 且无活跃分支降到 30s', () => {
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          'completed',
          'awaiting_approval',
          'pending',
        ])
      )
    ).toBe(AWAITING_APPROVAL_POLL_INTERVAL_MS)
    expect(AWAITING_APPROVAL_POLL_INTERVAL_MS).toBe(30_000)
  })

  it.each(['ready', 'running'])(
    'awaiting_approval 但并行分支有 %s 节点时保持 5s',
    (nodeStatus) => {
      expect(
        jobDetailPollInterval(
          detail('awaiting_approval', ['awaiting_approval', nodeStatus])
        )
      ).toBe(ACTIVE_POLL_INTERVAL_MS)
    }
  )

  it.each(['completed', 'failed', 'paused', 'cancelled'])(
    '终态 / 暂停 %s 停轮询',
    (status) => {
      expect(jobDetailPollInterval(detail(status, ['running']))).toBe(false)
    }
  )

  it('尚无数据时不轮询（首拉由 query 自身负责）', () => {
    expect(jobDetailPollInterval(undefined)).toBe(false)
    expect(jobDetailPollInterval(null)).toBe(false)
  })
})
