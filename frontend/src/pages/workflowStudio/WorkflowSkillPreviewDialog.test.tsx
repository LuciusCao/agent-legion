import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { getSkillDetail } from '../../executorApi'
import { WorkflowSkillPreviewDialog } from './WorkflowSkillPreviewDialog'

vi.mock('../../executorApi', () => ({ getSkillDetail: vi.fn() }))

beforeEach(() => {
  vi.mocked(getSkillDetail).mockResolvedValue({
    key: 'demo/review',
    ref: 'v1.2.0',
    commit: 'abc123',
    available: true,
    files: [
      { path: 'SKILL.md', size: 8, content: '# Skill', truncated: false },
      {
        path: 'references/rules.md',
        size: 7,
        content: '# Rules',
        truncated: false,
      },
    ],
  })
})

it('loads and switches between configured skill files', async () => {
  render(
    <WorkflowSkillPreviewDialog open skillKey="demo/review" onClose={vi.fn()} />
  )

  expect(await screen.findByText('# Skill')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /references\/rules.md/ }))

  expect(screen.getByText('# Rules')).toBeInTheDocument()
  expect(getSkillDetail).toHaveBeenCalledWith('demo/review')
})
