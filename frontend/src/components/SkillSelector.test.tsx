import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  defaultSkillDetail,
  mockFetchDirectories,
  mockGetSettings,
  mockGetSkillDetail,
  mockValidate,
  renderSelector,
  settingsWithRoot,
} from './SkillSelector.testUtils'

// 版本选择与技能根前缀（其余主题见姊妹文件：SkillSelector.directory /
// SkillSelector.validation，codex 四轮 P1 on #427 拆分）。

describe('SkillSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSettings.mockResolvedValue(settingsWithRoot('~/.agents/skills'))
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: [],
    })
    mockGetSkillDetail.mockResolvedValue(defaultSkillDetail())
  })

  it('fills the skill key and offers latest + tags in the version select after a successful validation', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ws-1/write-script',
      tags: ['v1.2.0', 'v1.1.0'],
      latest_tag: 'v1.2.0',
      locked_ref: 'abc123',
    })
    const onChange = vi.fn()
    const view = renderSelector({ onChange })

    // 等技能根前缀加载完成（此前输入保持禁用），只读前缀 + 相对目录名输入。
    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(screen.getByText('~/.agents/skills/ws-1/')).toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith('ws-1/write-script')
    )
    // 相对名拼上 workspace 技能根前缀（后端 validator 自行展开 ~）。
    expect(mockValidate).toHaveBeenCalledWith(
      '~/.agents/skills/ws-1/write-script'
    )
    // 回填后输入仍是发起校验的相对名（受控跟随绑定，codex r2 P1 on #427；
    // codex 三轮 P2 on #427：首段 workspaceId 与只读前缀重复，回显余段，
    // 与前缀拼回 = 原绑定路径，不再拼出 ws-1/ws-1/write-script）。
    view.rerenderWith({ value: 'ws-1/write-script', skillRef: '' })
    await waitFor(() => expect(input).toHaveValue('write-script'))
    expect(await screen.findByText('已锁定版本：abc123')).toBeInTheDocument()
    const versionSelect = await screen.findByLabelText('版本')
    await waitFor(() => expect(versionSelect).toBeEnabled())
    fireEvent.mouseDown(versionSelect)
    expect(
      await screen.findByRole('option', {
        name: 'latest（当前最新 tag：v1.2.0）',
      })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: 'v1.2.0（最新）' })
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'v1.1.0' })).toBeInTheDocument()
    expect(screen.queryByLabelText('可用 tag（参考）')).not.toBeInTheDocument()
  })

  it('writes the picked tag into ref via the version select (#410)', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ws-1/write-script',
      tags: ['v1.2.0', 'v1.1.0'],
      latest_tag: 'v1.2.0',
      locked_ref: null,
    })
    const onSkillRefChange = vi.fn()
    const view = renderSelector({ onSkillRefChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/write-script'
      )
    )
    view.rerenderWith({ value: 'ws-1/write-script', skillRef: '' })

    const versionSelect = await screen.findByLabelText('版本')
    await waitFor(() => expect(versionSelect).toBeEnabled())
    // 受控回显是 effect 驱动的异步同步：等回显完成再操作（ws 形态 key
    // 首段与只读前缀重复，回显余段——codex 三轮 P2 on #427）。
    await waitFor(() => expect(input).toHaveValue('write-script'))
    fireEvent.mouseDown(versionSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'v1.1.0' }))

    expect(onSkillRefChange).toHaveBeenCalledWith('v1.1.0')
  })

  it('keeps picking latest possible when no tags exist (degraded helper text)', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    const view = renderSelector()

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'write-script' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    view.rerenderWith({ value: 'ns/skill', skillRef: '' })

    const versionSelect = await screen.findByLabelText('版本')
    await waitFor(() => expect(versionSelect).toBeEnabled())
    // 降级：无 tag 时 latest 是唯一选项（无「当前最新 tag」标注）。
    fireEvent.mouseDown(versionSelect)
    expect(
      await screen.findByRole('option', { name: 'latest（跟随最新）' })
    ).toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(
      screen.getByText('仓库暂无 tag：latest 跟随仓库最新提交')
    ).toBeInTheDocument()
  })

  it('echoes the bound skill ref as the version value and loads its tags from the detail endpoint', async () => {
    mockGetSkillDetail.mockResolvedValue({
      available: true,
      commit: 'abc123def456',
      files: [],
      key: 'ns/skill',
      ref: 'main',
      tags: ['v1.3.0', 'v1.2.0'],
    } as ReturnType<typeof defaultSkillDetail>)
    renderSelector({ value: 'ns/skill', skillRef: 'v1.2.0' })

    await waitFor(() =>
      expect(mockGetSkillDetail).toHaveBeenCalledWith('ns/skill')
    )
    // 选中 tag 经 combobox 文本断言（未绑定校验流程时同样可选版本）。
    expect(screen.getByRole('combobox', { name: '版本' })).toHaveTextContent(
      'v1.2.0'
    )
    fireEvent.mouseDown(screen.getByLabelText('版本'))
    expect(
      await screen.findByRole('option', { name: 'v1.3.0（最新）' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: 'latest（当前最新 tag：v1.3.0）' })
    ).toBeInTheDocument()
  })

  it('normalizes an empty bound ref to latest in the version select (#322)', () => {
    renderSelector({ value: 'ns/skill', skillRef: '' })

    expect(screen.getByRole('combobox', { name: '版本' })).toHaveTextContent(
      'latest'
    )
  })

  it('disables the version select until a skill is bound', () => {
    renderSelector()

    expect(screen.getByLabelText('版本')).toHaveAttribute(
      'aria-disabled',
      'true'
    )
    expect(screen.getByText('先经上方校验选择 skill')).toBeInTheDocument()
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

  it('offers the workspace skill directories as datalist candidates', async () => {
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: ['review-questions', 'write-script'],
    })
    const { container } = renderSelector()

    await waitFor(() =>
      expect(
        container.querySelector(
          'datalist#skill-directory-options option[value="write-script"]'
        )
      ).not.toBeNull()
    )
    expect(
      container.querySelector(
        'datalist#skill-directory-options option[value="review-questions"]'
      )
    ).not.toBeNull()
  })
})
