import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { validateSkillPath } from '../api'
import { getInstanceSettings } from '../api/instanceSettings'
import type { InstanceSettingsResponse } from '../api/instanceSettings'
import { TestQueryProvider } from '../testing/testQueryClient'
import { SkillSelector } from './SkillSelector'

vi.mock('../api', () => ({
  validateSkillPath: vi.fn(),
}))

vi.mock('../api/instanceSettings', () => ({
  getInstanceSettings: vi.fn(),
}))

const mockValidate = vi.mocked(validateSkillPath)
const mockGetSettings = vi.mocked(getInstanceSettings)

function settingsWithRoot(skillsRoot: string): InstanceSettingsResponse {
  // 用例只关心 skills_root，其余实例设置字段不参与本组件逻辑。
  return { skills_root: skillsRoot } as InstanceSettingsResponse
}

function renderSelector(onChange: (key: string) => void = () => {}) {
  return render(
    <TestQueryProvider>
      <SkillSelector workspaceId="ws-1" value="" onChange={onChange} />
    </TestQueryProvider>
  )
}

describe('SkillSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSettings.mockResolvedValue(settingsWithRoot('~/.agents/skills'))
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
    renderSelector(onChange)

    // 等技能根前缀加载完成（此前输入保持禁用），只读前缀 + 相对目录名输入。
    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(screen.getByText('~/.agents/skills/ws-1/')).toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('ns/skill'))
    // 相对名拼上 workspace 技能根前缀（后端 validator 自行展开 ~）。
    expect(mockValidate).toHaveBeenCalledWith(
      '~/.agents/skills/ws-1/write-script'
    )
    expect(screen.getByText('当前锁定 ref：abc123')).toBeInTheDocument()
    expect(screen.getByLabelText('可用 tag（参考）')).toBeInTheDocument()
  })

  it('strips leading slashes from the relative name before composing', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    renderSelector()

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: '/write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/write-script'
      )
    )
  })

  it('composes the prefix from the instance settings skills_root', async () => {
    mockGetSettings.mockResolvedValue(settingsWithRoot('/data/skills/'))
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    renderSelector()

    expect(await screen.findByText('/data/skills/ws-1/')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Skill 目录名'), {
      target: { value: 'write-script' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '/data/skills/ws-1/write-script'
      )
    )
  })

  it('keeps the input disabled until the skills root finishes loading', () => {
    mockGetSettings.mockReturnValue(new Promise(() => {}))
    renderSelector()

    expect(screen.getByLabelText('Skill 目录名')).toBeDisabled()
    expect(screen.getByRole('button', { name: '校验' })).toBeDisabled()
  })

  it('falls back to the default root with a hint when instance settings fail to load', async () => {
    mockGetSettings.mockRejectedValue(new Error('HTTP 403'))
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    renderSelector()

    expect(
      await screen.findByText(/实例设置加载失败，技能根目录回退为默认/)
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Skill 目录名'), {
      target: { value: 'write-script' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/write-script'
      )
    )
  })

  it('shows the validation error when the path is invalid', async () => {
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const onChange = vi.fn()
    renderSelector(onChange)

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'nope' } })
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
    renderSelector()

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    const tagSelect = await screen.findByLabelText('可用 tag（参考）')
    fireEvent.mouseDown(tagSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'v1.1.0' }))

    expect(
      await screen.findByText(/tag 变更需通过 skills 同步流程生效/)
    ).toBeInTheDocument()
  })
})
