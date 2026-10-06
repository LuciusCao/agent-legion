import { describe, expect, it } from 'vitest'
import {
  ACTIVE_POLL_INTERVAL_MS,
  AWAITING_APPROVAL_POLL_INTERVAL_MS,
  jobDetailPollInterval,
} from './jobDetailPolling'
import type { JobDetail } from '../../types/jobTypes'

type NodeSpec = [key: string, status: string, after?: string[]]

function detail(
  status: string,
  nodes: NodeSpec[] = [['a', 'completed']]
): Pick<JobDetail, 'job' | 'nodes'> {
  return {
    job: { id: 'j1', status } as JobDetail['job'],
    nodes: nodes.map(
      ([key, nodeStatus, after = []]) =>
        ({
          node_key: key,
          status: nodeStatus,
          after,
        }) as JobDetail['nodes'][number]
    ),
  }
}

describe('jobDetailPollInterval (#965)', () => {
  it.each(['queued', 'running'])('活跃执行态 %s 保持 5s', (status) => {
    expect(jobDetailPollInterval(detail(status))).toBe(ACTIVE_POLL_INTERVAL_MS)
    expect(ACTIVE_POLL_INTERVAL_MS).toBe(5_000)
  })

  it('awaiting_approval 且只剩审批门下游的 pending 节点：降到 30s', () => {
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          ['a', 'completed'],
          ['gate', 'awaiting_approval', ['a']],
          ['c', 'pending', ['gate']],
        ])
      )
    ).toBe(AWAITING_APPROVAL_POLL_INTERVAL_MS)
    expect(AWAITING_APPROVAL_POLL_INTERVAL_MS).toBe(30_000)
  })

  it.each(['pending', 'stale'])(
    'awaiting_approval 但并行分支有可认领的 %s 节点（依赖全部完成）时保持 5s',
    (nodeStatus) => {
      expect(
        jobDetailPollInterval(
          detail('awaiting_approval', [
            ['gate', 'awaiting_approval'],
            ['a', 'completed'],
            ['b', nodeStatus, ['a']],
          ])
        )
      ).toBe(ACTIVE_POLL_INTERVAL_MS)
    }
  )

  it('not_applicable 依赖视同已落定；无依赖的 pending 根节点也可认领', () => {
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          ['gate', 'awaiting_approval'],
          ['skip', 'not_applicable'],
          ['b', 'pending', ['skip']],
        ])
      )
    ).toBe(ACTIVE_POLL_INTERVAL_MS)
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          ['gate', 'awaiting_approval'],
          ['root', 'pending'],
        ])
      )
    ).toBe(ACTIVE_POLL_INTERVAL_MS)
  })

  it('依赖仍在 pending / failed 的节点不算可认领', () => {
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          ['gate', 'awaiting_approval'],
          ['x', 'failed'],
          ['y', 'pending', ['x']],
        ])
      )
    ).toBe(AWAITING_APPROVAL_POLL_INTERVAL_MS)
  })

  it('awaiting_approval 但有 running 节点（快照竞态）时保持 5s', () => {
    expect(
      jobDetailPollInterval(
        detail('awaiting_approval', [
          ['gate', 'awaiting_approval'],
          ['b', 'running'],
        ])
      )
    ).toBe(ACTIVE_POLL_INTERVAL_MS)
  })

  it.each(['completed', 'failed', 'paused', 'cancelled'])(
    '终态 / 暂停 %s 停轮询',
    (status) => {
      expect(jobDetailPollInterval(detail(status, [['a', 'running']]))).toBe(
        false
      )
    }
  )

  it('尚无数据时不轮询（首拉由 query 自身负责）', () => {
    expect(jobDetailPollInterval(undefined)).toBe(false)
    expect(jobDetailPollInterval(null)).toBe(false)
  })
})
