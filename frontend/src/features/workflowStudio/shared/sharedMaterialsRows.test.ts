import { describe, expect, it } from 'vitest'
import {
  buildSharedMaterialFileRows,
  isRowPropagatable,
} from './sharedMaterialsRows'
import type { WorkspaceSharedMaterialsResponse } from '../../../api'

const base: WorkspaceSharedMaterialsResponse = {
  workspace_id: 'ws',
  map: {
    version: 1,
    materials: [
      {
        source: 'references/a.md',
        skills: [{ skill: 'skill-a', status: 'pending_sync' }],
      },
      {
        source: 'references/gone.md',
        skills: [{ skill: 'skill-b', status: 'synced' }],
      },
    ],
  },
  files: [
    { path: 'references/a.md', size: 10, modified_at: '2026-09-01T00:00:00Z' },
    {
      path: 'references/loose.md',
      size: 5,
      modified_at: '2026-09-01T00:00:00Z',
    },
  ],
}

describe('buildSharedMaterialFileRows', () => {
  it('joins files with their map skills and appends missing sources', () => {
    const rows = buildSharedMaterialFileRows(base)
    expect(rows.map((row) => row.path)).toEqual([
      'references/a.md',
      'references/loose.md',
      'references/gone.md',
    ])
    expect(rows[0].skills.map((s) => s.skill)).toEqual(['skill-a'])
    expect(rows[0].missingSource).toBe(false)
    expect(rows[1].skills).toEqual([])
    expect(rows[2].missingSource).toBe(true)
    expect(rows[2].size).toBeNull()
  })

  it('tolerates a null map and empty files', () => {
    expect(
      buildSharedMaterialFileRows({
        workspace_id: 'ws',
        map: null,
        files: [],
      })
    ).toEqual([])
  })
})

describe('isRowPropagatable', () => {
  it('is true only for non-missing rows with behind/missing skills', () => {
    const rows = buildSharedMaterialFileRows(base)
    expect(isRowPropagatable(rows[0])).toBe(true) // pending_sync
    expect(isRowPropagatable(rows[1])).toBe(false) // 未映射
    expect(isRowPropagatable(rows[2])).toBe(false) // 缺失源
    expect(
      isRowPropagatable({
        path: 'x',
        size: 1,
        modifiedAt: null,
        skills: [
          { skill: 's', status: 'synced' },
          { skill: 't', status: 'skill_not_found' },
        ],
        missingSource: false,
      })
    ).toBe(false) // 一致 + skill 缺失都不算「可传播」
    expect(
      isRowPropagatable({
        path: 'x',
        size: 1,
        modifiedAt: null,
        skills: [{ skill: 's', status: 'missing_in_skill' }],
        missingSource: false,
      })
    ).toBe(true)
  })
})
