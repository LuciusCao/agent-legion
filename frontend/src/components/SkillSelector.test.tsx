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
    // 回填后输入仍是发起校验的相对名（受控跟随绑定，codex r2 P1 on #427：
    // key 即技能根下的相对路径，与本 workspace 路径校验回填的首段
    // workspaceId 一起原样回显——首段正是校验时的目录前缀组成段）。
    view.rerenderWith({ value: 'ws-1/write-script', skillRef: '' })
    await waitFor(() => expect(input).toHaveValue('ws-1/write-script'))
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
    // 受控回显是 effect 驱动的异步同步：等回显完成再操作（key 全段即
    // 技能根下的相对路径）。
    await waitFor(() => expect(input).toHaveValue('ws-1/write-script'))
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

  it('shows the validation error when the path is invalid', async () => {
    // #427：invalid 结果（key 为 null）仅在节点尚未绑定 skill（value 空）时
    // 展示——与「按 key 关联」规则一致，无绑定的输入错误不跨节点泄漏。
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

  it("ignores the previous node's validation result after the bound key changes (#427)", async () => {
    // codex P1 on #427：检查器不卸载、直接切换到另一个 Agent 节点——节点 A
    // 校验过的 result 会跨节点残留。版本下拉的选项必须来自 B 自己的详情
    // （不走 A 的 tags），选版本写入的也是 B 的绑定回调。
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/node-a-skill',
      skill_key: 'ws-1/skill-a',
      tags: ['v9.9.9'],
      latest_tag: 'v9.9.9',
      locked_ref: 'aaabbb',
    })
    mockGetSkillDetail.mockResolvedValue({
      available: true,
      commit: 'def456',
      files: [],
      key: 'ws-1/skill-b',
      ref: 'main',
      tags: ['v2.0.0', 'v1.0.0'],
    } as SkillDetail)
    const onSkillRefChange = vi.fn()
    const view = renderSelector({ onSkillRefChange })

    // 节点 A：校验成功，tags 来自校验响应。
    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-a' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith('~/.agents/skills/ws-1/skill-a')
    )
    view.rerenderWith({ value: 'ws-1/skill-a', skillRef: '' })
    let versionSelect = await screen.findByLabelText('版本')
    await waitFor(() => expect(versionSelect).toBeEnabled())
    expect(screen.getByText('已锁定版本：aaabbb')).toBeInTheDocument()

    // 切换到节点 B（不同 skill key，检查器不卸载）：A 的校验结果按 key 失效。
    view.rerenderWith({ value: 'ws-1/skill-b', skillRef: '' })
    await waitFor(() =>
      expect(mockGetSkillDetail).toHaveBeenCalledWith('ws-1/skill-b')
    )
    expect(screen.queryByText('已锁定版本：aaabbb')).not.toBeInTheDocument()
    versionSelect = screen.getByLabelText('版本')
    fireEvent.mouseDown(versionSelect)
    // 版本选项来自 B 自己的详情端点，A 的 v9.9.9 不得出现。
    expect(
      await screen.findByRole('option', { name: 'v2.0.0（最新）' })
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'v1.0.0' })).toBeInTheDocument()
    expect(
      screen.queryByRole('option', { name: /v9\.9\.9/ })
    ).not.toBeInTheDocument()

    // 选择版本写入的是 B 的绑定回调（值本身是 B 的 tag）。
    fireEvent.click(await screen.findByRole('option', { name: 'v1.0.0' }))
    expect(onSkillRefChange).toHaveBeenCalledWith('v1.0.0')
    expect(onSkillRefChange).not.toHaveBeenCalledWith('v9.9.9')
  })

  it('keeps an invalid result visible only while the node has no bound skill (#427)', async () => {
    // codex P1 on #427 的另一面：无效输入的错误提示属于未绑定状态；切到
    // 已绑定其他 key 的节点后，旧错误不得继续展示（B 的绑定不受 A 的
    // 校验失败影响）。
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const view = renderSelector()

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'SKILL.md 不存在'
    )

    view.rerenderWith({ value: 'ws-1/skill-b', skillRef: '' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
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

  it('shows the bound skill directory in the input when echoing an existing binding (codex r2 P1 on #427)', async () => {
    // 打开已有绑定：目录输入回显 key 的全段（技能根下的完整相对路径，
    // 二轮复审 P3-3 on #427——不再假设首段是 workspaceId 而截断）。
    renderSelector({ value: 'ws-1/question_analysis/generate_key_info' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('ws-1/question_analysis/generate_key_info')
  })

  it('echoes a group-form key as the full path under the skills root (independent review P3-3 on #427)', async () => {
    // demo workflow 的 group 形态 key（YAML 手写/历史数据，首段不是
    // workspaceId）：回显必须保留全段，剥掉首段会显示成 write-script、
    // 与 workspace 前缀拼出的路径不再是原绑定路径。
    const view = renderSelector({
      value: 'education-video-problems-generation/write-script',
    })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue(
      'education-video-problems-generation/write-script'
    )
    // 两种形态都验证：ws-1/write-script（校验器对 <skills_root>/<ws>/ 下
    // 路径构造的 key）同样原样全段回显。
    view.rerenderWith({
      value: 'ws-1/write-script',
    })
    await waitFor(() => expect(input).toHaveValue('ws-1/write-script'))
  })

  it('follows the bound directory when the inspector switches nodes (codex r2 P1 on #427)', async () => {
    // 节点 A（skill-a）校验回填后切换到节点 B（skill-b）：输入不得残留 A
    // 的目录名——否则用户在 B 上点「校验」会把 A 误绑到 B。
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill-a',
      skill_key: 'ws-1/skill-a',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    const onChange = vi.fn()
    const view = renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-a' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('ws-1/skill-a'))
    expect(input).toHaveValue('skill-a')

    // 切换到节点 B（检查器不卸载，仅 value 变化）：输入跟随 B 的绑定
    // （key 全段回显——ws-1 前缀下输入 ws-1/skill-b 与原绑定路径一致）。
    view.rerenderWith({ value: 'ws-1/skill-b' })
    expect(input).toHaveValue('ws-1/skill-b')
    // 等回显查询落地，避免其解析落在 act 外告警。
    await waitFor(() =>
      expect(mockGetSkillDetail).toHaveBeenCalledWith('ws-1/skill-b')
    )

    // 此时点「校验」校验的是 B 的目录，不会再把 A 绑上去（B 的输入即
    // workspace 段 + 目录名，拼前缀 = 原绑定路径，validator 归一化重复段）。
    mockValidate.mockClear()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '校验' }))
    })
    expect(mockValidate).toHaveBeenCalledWith(
      '~/.agents/skills/ws-1/ws-1/skill-b'
    )
  })

  it('keeps user edits in the input while the binding stays unchanged', async () => {
    // 绑定未变（普通编辑场景）：本地输入不被外部同名 props 重置打断。
    renderSelector({ value: 'ws-1/skill-a' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('ws-1/skill-a')
    fireEvent.change(input, { target: { value: 'skill-a-custom' } })
    expect(input).toHaveValue('skill-a-custom')
  })

  it('discards a late validation response after the inspector switches nodes (codex r2 P1 on #427)', async () => {
    // 节点 A 的校验在飞时切换到节点 B：A 的迟到响应不得触发 onChange（其
    // 回写基于 A 的旧草稿 YAML，会覆盖等待期间的编辑）、也不得弹 A 的结果。
    let resolveA!: (value: SkillValidateResponse) => void
    mockValidate.mockImplementationOnce(
      () =>
        new Promise<SkillValidateResponse>((resolve) => {
          resolveA = resolve
        })
    )
    const onChange = vi.fn()
    const view = renderSelector({ onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-a' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(1))

    // 切换到节点 B（A 的请求仍在飞）。
    view.rerenderWith({ value: 'ws-1/skill-b' })

    await act(async () => {
      resolveA({
        valid: true,
        path: '/abs/skill-a',
        skill_key: 'ws-1/skill-a',
        tags: ['v9.9.9'],
        latest_tag: 'v9.9.9',
        locked_ref: 'aaabbb',
      })
    })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('已锁定版本：aaabbb')).not.toBeInTheDocument()
    // 在飞的 loading 态也不得残留。
    expect(screen.getByRole('button', { name: '校验' })).toBeInTheDocument()
  })

  it('shows the invalid-result error when re-binding an already bound node fails (independent review P2 on #427)', async () => {
    // 已绑定 skill 的节点换绑时输错目录名：key 未变（value 仍为旧绑定），
    // invalid 结果按「校验发起时 value 快照」归属当前节点——错误必须可见，
    // 不得零反馈（develop 上的既有行为，key-gating 引入的回归）。
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const onChange = vi.fn()
    renderSelector({ value: 'ws-1/skill-b', onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('ws-1/skill-b')
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'SKILL.md 不存在'
    )
    // 换绑失败不回写 key：绑定保持原值。
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows the invalid-result error for a failed validation on a bound node that keeps its key (independent review P2 on #427)', async () => {
    // 同一 key 重新校验失败（如 tag 化目录被删）：value 快照命中，错误可见。
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/broken',
      error: 'skill path is not a directory',
    })
    renderSelector({ value: 'ws-1/skill-b' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('ws-1/skill-b')
    fireEvent.change(input, { target: { value: 'skill-b' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'skill path is not a directory'
    )
  })

  it('keeps the invalid-result error on its own node when switching to a node with the same bound key (independent review P3-1 on #427)', async () => {
    // 节点 A（绑定 ws-1/skill-b）换绑时输错：错误属于「那次输入」发生的
    // 绑定上下文。切到同样绑定 ws-1/skill-b 的节点 B——按值匹配会让 A 的
    // 输入错误继续显示在 B 上（误报）；按 key 快照归属后仍命中（同 key 是
    // 语义边界），但切到绑定不同 key 的节点 B' 时必须消失。
    mockValidate.mockResolvedValue({
      valid: false,
      path: '/abs/nope',
      error: 'SKILL.md 不存在',
    })
    const view = renderSelector({ value: 'ws-1/skill-b' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('ws-1/skill-b')
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'SKILL.md 不存在'
    )

    // 同绑定 key 的另一节点：错误仍显示（同 key 语义边界内）。
    view.rerenderWith({ value: 'ws-1/skill-b' })
    expect(screen.getByRole('alert')).toHaveTextContent('SKILL.md 不存在')

    // 换绑成功后（绑定变成新 key）：旧输入错误随 key 变化消失。
    view.rerenderWith({ value: 'ws-1/skill-c' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
