import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSkillDetail } from '../api/agentCatalogApi'
import { fetchSkillDirectories, validateSkillPath } from '../api'
import { getInstanceSettings } from '../api/instanceSettings'
import type { InstanceSettingsResponse } from '../api/instanceSettings'
import { TestQueryProvider } from '../testing/testQueryClient'
import type { SkillDetail } from '../types/agentCatalogTypes'
import type { SkillValidateResponse } from '../types'
import { SkillSelector } from './SkillSelector'

vi.mock('../api', () => ({
  validateSkillPath: vi.fn(),
  fetchSkillDirectories: vi.fn(),
}))

vi.mock('../api/agentCatalogApi', () => ({
  getSkillDetail: vi.fn(),
}))

vi.mock('../api/instanceSettings', () => ({
  getInstanceSettings: vi.fn(),
}))

const mockValidate = vi.mocked(validateSkillPath)
const mockFetchDirectories = vi.mocked(fetchSkillDirectories)
const mockGetSkillDetail = vi.mocked(getSkillDetail)
const mockGetSettings = vi.mocked(getInstanceSettings)

function settingsWithRoot(skillsRoot: string): InstanceSettingsResponse {
  // 用例只关心 skills_root，其余实例设置字段不参与本组件逻辑。
  return { skills_root: skillsRoot } as InstanceSettingsResponse
}

function renderSelector(
  props: Partial<{
    value: string
    onChange: (key: string) => void
    skillRef: string
    onSkillRefChange: (ref: string) => void
  }> = {}
) {
  const view = render(
    <TestQueryProvider>
      <SkillSelector
        workspaceId="ws-1"
        value={props.value ?? ''}
        onChange={props.onChange ?? (() => {})}
        skillRef={props.skillRef ?? ''}
        onSkillRefChange={props.onSkillRefChange ?? (() => {})}
      />
    </TestQueryProvider>
  )
  // 宿主（WorkflowNodeSkillEditor）会把校验回填的 key 作为 value 传回；
  // 单测用 rerender 模拟这一环。
  return {
    ...view,
    rerenderWith: (next: typeof props) =>
      view.rerender(
        <TestQueryProvider>
          <SkillSelector
            workspaceId="ws-1"
            value={next.value ?? ''}
            onChange={next.onChange ?? props.onChange ?? (() => {})}
            skillRef={next.skillRef ?? ''}
            onSkillRefChange={
              next.onSkillRefChange ?? props.onSkillRefChange ?? (() => {})
            }
          />
        </TestQueryProvider>
      ),
  }
}

describe('SkillSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSettings.mockResolvedValue(settingsWithRoot('~/.agents/skills'))
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: [],
    })
    mockGetSkillDetail.mockResolvedValue({
      available: true,
      commit: 'abc123def456',
      files: [],
      key: 'ns/skill',
      ref: 'main',
      tags: [],
    } as SkillDetail)
  })

  it('fills the skill key and offers latest + tags in the version select after a successful validation', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
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

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('ns/skill'))
    // 相对名拼上 workspace 技能根前缀（后端 validator 自行展开 ~）。
    expect(mockValidate).toHaveBeenCalledWith(
      '~/.agents/skills/ws-1/write-script'
    )
    expect(screen.getByText('已锁定版本：abc123')).toBeInTheDocument()
    // 宿主回填 key 后版本下拉可用：latest + 全部 tag（替代旧「可用 tag（参考）」）。
    view.rerenderWith({ value: 'ns/skill', skillRef: '' })
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
      skill_key: 'ns/skill',
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
    view.rerenderWith({ value: 'ns/skill', skillRef: '' })

    const versionSelect = await screen.findByLabelText('版本')
    await waitFor(() => expect(versionSelect).toBeEnabled())
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
    } as SkillDetail)
    renderSelector({ value: 'ns/skill', skillRef: 'v1.2.0' })

    await waitFor(() => expect(mockGetSkillDetail).toHaveBeenCalledWith('ns/skill'))
    // 选中 tag 经 combobox 文本断言（未绑定校验流程时同样可选版本）。
    expect(
      screen.getByRole('combobox', { name: '版本' })
    ).toHaveTextContent('v1.2.0')
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

    expect(
      screen.getByRole('combobox', { name: '版本' })
    ).toHaveTextContent('latest')
  })

  it('disables the version select until a skill is bound', () => {
    renderSelector()

    expect(screen.getByLabelText('版本')).toHaveAttribute('aria-disabled', 'true')
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

  it('shows the validation error when the path is invalid', async () => {
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const onChange = vi.fn()
    renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'SKILL.md 不存在'
    )
    expect(onChange).not.toHaveBeenCalled()
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

  it('validates immediately when a datalist candidate is picked', async () => {
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: ['write-script'],
    })
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/write-script',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    const onChange = vi.fn()
    const { container } = renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    // 等候选加载完成（datalist option 出现即组件已拿到 directories）。
    await waitFor(() =>
      expect(
        container.querySelector(
          'datalist#skill-directory-options option[value="write-script"]'
        )
      ).not.toBeNull()
    )
    fireEvent.change(input, { target: { value: 'write-script' } })

    // 选中候选即触发校验回填，无需点「校验」按钮。
    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/write-script'
      )
    )
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith('ns/write-script')
    )
  })

  it('does not auto-validate typed text that matches no candidate', async () => {
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: ['write-script'],
    })
    const { container } = renderSelector()

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() =>
      expect(
        container.querySelector(
          'datalist#skill-directory-options option[value="write-script"]'
        )
      ).not.toBeNull()
    )
    fireEvent.change(input, { target: { value: 'write' } })

    expect(mockValidate).not.toHaveBeenCalled()
  })

  it('ignores a stale validation response when a newer pick is in flight', async () => {
    // 前缀关联候选（review / review-questions）快速连选：A 先发、B 后发，
    // B 先返回、A 晚返回——A 是过期响应，不得覆盖 B 的回填（codex P1 on #336）。
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: ['review', 'review-questions'],
    })
    let resolveA!: (value: SkillValidateResponse) => void
    let resolveB!: (value: SkillValidateResponse) => void
    mockValidate
      .mockImplementationOnce(
        () =>
          new Promise<SkillValidateResponse>((resolve) => {
            resolveA = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise<SkillValidateResponse>((resolve) => {
            resolveB = resolve
          })
      )
    const onChange = vi.fn()
    const { container } = renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() =>
      expect(
        container.querySelector(
          'datalist#skill-directory-options option[value="review-questions"]'
        )
      ).not.toBeNull()
    )
    fireEvent.change(input, { target: { value: 'review' } })
    fireEvent.change(input, { target: { value: 'review-questions' } })
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(2))

    // 后选的 B 先返回：正常回填。
    resolveB({
      valid: true,
      path: '/abs/review-questions',
      skill_key: 'ws-1/review-questions',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith('ws-1/review-questions')
    )

    // 先选的 A 晚返回（且为失败）：过期响应不得二次回填、不得弹错误、
    // 也不得把按钮卡回「校验中」。
    await act(async () => {
      resolveA({ valid: false, path: '/abs/review', error: 'stale failure' })
    })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '校验' })).toBeInTheDocument()
  })

  it('invalidates an in-flight validation when the input keeps being edited', async () => {
    // 选中候选 review（校验在飞）后继续手打到非候选值 review-custom：
    // 迟到响应不得把 review 的 key 回填到当前输入之上（codex P1 on #341）。
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: ['review'],
    })
    let resolveA!: (value: SkillValidateResponse) => void
    mockValidate.mockImplementationOnce(
      () =>
        new Promise<SkillValidateResponse>((resolve) => {
          resolveA = resolve
        })
    )
    const onChange = vi.fn()
    const { container } = renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() =>
      expect(
        container.querySelector(
          'datalist#skill-directory-options option[value="review"]'
        )
      ).not.toBeNull()
    )
    fireEvent.change(input, { target: { value: 'review' } })
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(1))
    fireEvent.change(input, { target: { value: 'review-custom' } })
    expect(mockValidate).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveA({
        valid: true,
        path: '/abs/review',
        skill_key: 'ws-1/review',
        tags: [],
        latest_tag: null,
        locked_ref: null,
      })
    })
    expect(onChange).not.toHaveBeenCalled()
  })
})
