import { describe, expect, it } from 'vitest'
import { buildSharedMaterialFileRows } from './sharedMaterialsRows'
import {
  groupSharedMaterialFileRows,
  rowDisplayPath,
} from './sharedMaterialsGroups'

describe('groupSharedMaterialFileRows', () => {
  it('groups by top-level dir in references→scripts→其他 order', () => {
    const rows = buildSharedMaterialFileRows({
      workspace_id: 'ws',
      map: { version: 1, materials: [] },
      files: [
        { path: 'notes.md', size: 1, modified_at: '2026-09-01T00:00:00Z' },
        { path: 'scripts/x.sh', size: 1, modified_at: '2026-09-01T00:00:00Z' },
        {
          path: 'references/a.md',
          size: 1,
          modified_at: '2026-09-01T00:00:00Z',
        },
        { path: 'docs/guide.md', size: 1, modified_at: '2026-09-01T00:00:00Z' },
      ],
    })
    const groups = groupSharedMaterialFileRows(rows)
    expect(groups.map((g) => g.key)).toEqual(['references', 'scripts', 'other'])
    expect(groups.map((g) => g.label)).toEqual(['参考材料', '脚本', '其他'])
    expect(groups[0].description).toContain('_shared/references/')
    expect(groups[1].description).toContain('_shared/scripts/')
    expect(groups[2].description).toContain('_shared/')
    expect(groups[2].rows.map((r) => r.path)).toEqual([
      'notes.md',
      'docs/guide.md',
    ])
  })

  it('omits empty groups and keeps missing-source rows in their dir group', () => {
    const rows = buildSharedMaterialFileRows({
      workspace_id: 'ws',
      map: {
        version: 1,
        materials: [
          {
            source: 'references/gone.md',
            skills: [{ skill: 'skill-b', status: 'synced' }],
          },
        ],
      },
      files: [
        {
          path: 'references/a.md',
          size: 10,
          modified_at: '2026-09-01T00:00:00Z',
        },
      ],
    })
    const groups = groupSharedMaterialFileRows(rows)
    expect(groups.map((g) => g.key)).toEqual(['references'])
    // 缺失源 references/gone.md 留在 references/ 组内。
    expect(groups[0].rows.map((r) => r.path)).toContain('references/gone.md')
    expect(groupSharedMaterialFileRows([])).toEqual([])
  })
})

describe('rowDisplayPath', () => {
  const fileRow = (path: string, missingSource = false) => ({
    path,
    size: 1,
    modifiedAt: null,
    skills: [],
    missingSource,
  })

  it('strips the group-dir prefix inside material groups', () => {
    expect(rowDisplayPath('references', fileRow('references/a/b.md'))).toBe(
      'a/b.md'
    )
    expect(rowDisplayPath('scripts', fileRow('scripts/x.sh'))).toBe('x.sh')
  })

  it('keeps full paths for 其他 rows and missing-source rows', () => {
    expect(rowDisplayPath('other', fileRow('docs/guide.md'))).toBe(
      'docs/guide.md'
    )
    expect(rowDisplayPath('other', fileRow('notes.md'))).toBe('notes.md')
    expect(
      rowDisplayPath('references', fileRow('references/gone.md', true))
    ).toBe('references/gone.md')
  })
})
