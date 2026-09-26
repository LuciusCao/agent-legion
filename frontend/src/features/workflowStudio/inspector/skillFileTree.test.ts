import { describe, expect, it } from 'vitest'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import {
  buildSkillFileTree,
  SKILL_CORE_FILE_ORDER,
  skillCoreFileRank,
  skillFileName,
  splitSkillCoreFiles,
} from './skillFileTree'

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

describe('SKILL_CORE_FILE_ORDER', () => {
  it('pins SKILL.md first and contract.yaml second (issue #676)', () => {
    expect(SKILL_CORE_FILE_ORDER).toEqual(['SKILL.md', 'contract.yaml'])
  })
})

describe('skillCoreFileRank', () => {
  it('ranks core files by table order', () => {
    expect(skillCoreFileRank('SKILL.md')).toBe(0)
    expect(skillCoreFileRank('contract.yaml')).toBe(1)
  })

  it('ranks non-core files after the whole table', () => {
    expect(skillCoreFileRank('notes.md')).toBe(SKILL_CORE_FILE_ORDER.length)
    expect(skillCoreFileRank('references/SKILL.md')).toBe(
      SKILL_CORE_FILE_ORDER.length
    )
  })
})

describe('splitSkillCoreFiles', () => {
  it('pins core files in table order at the root regardless of input order', () => {
    const { core, rest } = splitSkillCoreFiles(
      [
        skillFile('zzz.md'),
        skillFile('contract.yaml'),
        skillFile('SKILL.md'),
        skillFile('aaa.md'),
      ],
      true
    )
    expect(core.map((file) => file.path)).toEqual(['SKILL.md', 'contract.yaml'])
    // 其余根级文件保持 path localeCompare。
    expect(rest.map((file) => file.path)).toEqual(['aaa.md', 'zzz.md'])
  })

  it('keeps the localeCompare order when the root has no core files', () => {
    const { core, rest } = splitSkillCoreFiles(
      [skillFile('b.md'), skillFile('a.md')],
      true
    )
    expect(core).toEqual([])
    expect(rest.map((file) => file.path)).toEqual(['a.md', 'b.md'])
  })

  it('sorts everything by localeCompare and pins nothing below the root', () => {
    const { core, rest } = splitSkillCoreFiles(
      [
        skillFile('references/SKILL.md'),
        skillFile('references/contract.yaml'),
        skillFile('references/a.md'),
      ],
      false
    )
    expect(core).toEqual([])
    // 子目录内同名核心文件不置顶，维持既有全路径排序。
    expect(rest.map((file) => file.path)).toEqual([
      'references/a.md',
      'references/contract.yaml',
      'references/SKILL.md',
    ])
  })

  it('does not mutate the input file list', () => {
    const files = [skillFile('b.md'), skillFile('SKILL.md'), skillFile('a.md')]
    splitSkillCoreFiles(files, true)
    expect(files.map((file) => file.path)).toEqual(['b.md', 'SKILL.md', 'a.md'])
  })
})
