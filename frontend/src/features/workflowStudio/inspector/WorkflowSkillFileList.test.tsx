import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import { WorkflowSkillFileList } from './WorkflowSkillFileList'

function skillFile(path: string): SkillFile {
  return { path, size: 8, content: `# ${path}`, truncated: false }
}

const files = [
  skillFile('SKILL.md'),
  skillFile('references/rules.md'),
  skillFile('references/deep/notes.md'),
  skillFile('scripts/validate.py'),
]

describe('WorkflowSkillFileList', () => {
  it('renders the flat path list as a directory tree', () => {
    render(
      <WorkflowSkillFileList
        files={files}
        selected={undefined}
        onSelect={() => {}}
      />
    )

    // 目录节点与缩进的文件节点都出现；文件只显示 basename。
    expect(screen.getByRole('button', { name: 'SKILL.md' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'references' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )
    expect(screen.getByRole('button', { name: 'rules.md' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'deep' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'notes.md' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'scripts' })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'validate.py' })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /references\/rules/ })
    ).not.toBeInTheDocument()
  })

  it('collapses and re-expands a directory', () => {
    render(
      <WorkflowSkillFileList
        files={files}
        selected={undefined}
        onSelect={() => {}}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'references' }))
    expect(screen.getByRole('button', { name: 'references' })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
    expect(
      screen.queryByRole('button', { name: 'rules.md' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'deep' })
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'references' }))
    expect(screen.getByRole('button', { name: 'rules.md' })).toBeInTheDocument()
  })

  it('collapses a nested directory without hiding its parent siblings', () => {
    render(
      <WorkflowSkillFileList
        files={files}
        selected={undefined}
        onSelect={() => {}}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'deep' }))
    expect(
      screen.queryByRole('button', { name: 'notes.md' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'rules.md' })).toBeInTheDocument()
  })

  it('selects a file by its full relative path and marks the selection outlined', () => {
    const onSelect = vi.fn()
    render(
      <WorkflowSkillFileList
        files={files}
        selected={files[1]}
        onSelect={onSelect}
      />
    )

    expect(screen.getByRole('button', { name: 'rules.md' })).toHaveClass(
      'MuiButton-outlined'
    )
    expect(screen.getByRole('button', { name: 'SKILL.md' })).not.toHaveClass(
      'MuiButton-outlined'
    )
    fireEvent.click(screen.getByRole('button', { name: 'notes.md' }))
    expect(onSelect).toHaveBeenCalledWith('references/deep/notes.md')
  })
})
