import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { validateSkillPath } from '../api'
import { SkillSelector } from './SkillSelector'

vi.mock('../api', () => ({
  validateSkillPath: vi.fn(),
}))

const mockValidate = vi.mocked(validateSkillPath)

describe('SkillSelector', () => {
  beforeEach(() => {
    mockValidate.mockReset()
  })

  it('fills the skill key and shows tags after a successful validation', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: ['v1.2.0', 'v1.1.0'],
      latest_tag: 'v1.2.0',
      locked_ref: 'abc123',
    })
    const onChange = vi.fn()
    render(<SkillSelector value="" onChange={onChange} />)

    fireEvent.change(screen.getByLabelText('Skill 路径（绝对路径）'), {
      target: { value: '/abs/skill' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('ns/skill'))
    expect(mockValidate).toHaveBeenCalledWith('/abs/skill')
    expect(screen.getByText('当前锁定 ref：abc123')).toBeInTheDocument()
    expect(screen.getByLabelText('可用 tag（参考）')).toBeInTheDocument()
  })

  it('shows the validation error when the path is invalid', async () => {
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const onChange = vi.fn()
    render(<SkillSelector value="" onChange={onChange} />)

    fireEvent.change(screen.getByLabelText('Skill 路径（绝对路径）'), {
      target: { value: '/abs/nope' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'SKILL.md 不存在'
    )
    expect(onChange).not.toHaveBeenCalled()
  })

  it('notes that tag changes go through the skills sync flow', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: ['v1.2.0', 'v1.1.0'],
      latest_tag: 'v1.2.0',
      locked_ref: null,
    })
    render(<SkillSelector value="" onChange={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Skill 路径（绝对路径）'), {
      target: { value: '/abs/skill' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    const tagSelect = await screen.findByLabelText('可用 tag（参考）')
    fireEvent.mouseDown(tagSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'v1.1.0' }))

    expect(
      await screen.findByText(/tag 变更需通过 skills 同步流程生效/)
    ).toBeInTheDocument()
  })
})
