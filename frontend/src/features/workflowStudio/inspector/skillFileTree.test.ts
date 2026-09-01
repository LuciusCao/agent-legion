import { describe, expect, it } from 'vitest'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import { buildSkillFileTree, skillFileName } from './skillFileTree'

function skillFile(path: string): SkillFile {
  return { path, size: 8, content: `# ${path}`, truncated: false }
}

describe('buildSkillFileTree', () => {
  it('groups nested paths into directory nodes with root files at the top level', () => {
    const root = buildSkillFileTree([
      skillFile('SKILL.md'),
      skillFile('references/rules.md'),
      skillFile('references/deep/notes.md'),
      skillFile('scripts/validate.py'),
    ])

    expect(root.path).toBe('')
    expect(root.files.map((file) => file.path)).toEqual(['SKILL.md'])
    expect(root.dirs.map((dir) => dir.name).sort()).toEqual([
      'references',
      'scripts',
    ])
    const references = root.dirs.find((dir) => dir.name === 'references')
    expect(references?.path).toBe('references')
    expect(references?.files.map((file) => file.path)).toEqual([
      'references/rules.md',
    ])
    const deep = references?.dirs.find((dir) => dir.name === 'deep')
    expect(deep?.path).toBe('references/deep')
    expect(deep?.files.map((file) => file.path)).toEqual([
      'references/deep/notes.md',
    ])
  })

  it('reuses an existing directory node for sibling files', () => {
    const root = buildSkillFileTree([
      skillFile('references/a.md'),
      skillFile('references/b.md'),
    ])
    expect(root.dirs).toHaveLength(1)
    expect(root.dirs[0].files.map((file) => file.path)).toEqual([
      'references/a.md',
      'references/b.md',
    ])
  })
})

describe('skillFileName', () => {
  it('returns the last path segment', () => {
    expect(skillFileName('references/deep/notes.md')).toBe('notes.md')
    expect(skillFileName('SKILL.md')).toBe('SKILL.md')
  })
})
