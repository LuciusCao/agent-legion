import { describe, it, expect } from 'vitest'
import { filterRelevantRuns } from './jobRuns'

const nodes = [
  { node_key: 'a', created_at: '2026-06-10T08:00:00Z' },
  { node_key: 'b', created_at: '2026-06-10T08:00:00Z' },
]

describe('filterRelevantRuns', () => {
  it('keeps runs that started at or after the node was created', () => {
    const runs = [
      { node_key: 'a', started_at: '2026-06-10T08:00:00Z' },
      { node_key: 'a', started_at: '2026-06-10T08:00:01Z' },
    ]
    expect(filterRelevantRuns(runs, nodes)).toEqual(runs)
  })

  it('drops runs that predate the current node incarnation', () => {
    const runs = [
      { node_key: 'a', started_at: '2026-06-09T08:00:00Z' },
      { node_key: 'a', started_at: '2026-06-10T08:00:00Z' },
    ]
    expect(filterRelevantRuns(runs, nodes)).toEqual([runs[1]])
  })

  it('handles SQLite current_timestamp format', () => {
    const sqliteNodes = [{ node_key: 'a', created_at: '2026-06-10 08:00:00' }]
    const runs = [
      { node_key: 'a', started_at: '2026-06-09T08:00:00Z' },
      { node_key: 'a', started_at: '2026-06-10T08:00:00Z' },
    ]
    expect(filterRelevantRuns(runs, sqliteNodes)).toEqual([runs[1]])
  })

  it('matches DagGraphNode shape using key instead of node_key', () => {
    const dagNodes = [{ key: 'a', created_at: '2026-06-10T08:00:00Z' }]
    const runs = [
      { node_key: 'a', started_at: '2026-06-09T08:00:00Z' },
      { node_key: 'a', started_at: '2026-06-10T08:00:00Z' },
    ]
    expect(filterRelevantRuns(runs, dagNodes)).toEqual([runs[1]])
  })

  it('keeps runs whose node has no created_at to stay backward compatible', () => {
    const runs = [{ node_key: 'a', started_at: '2026-06-09T08:00:00Z' }]
    expect(
      filterRelevantRuns(runs, [
        { node_key: 'a', created_at: undefined as unknown as string },
      ])
    ).toEqual(runs)
  })
})
