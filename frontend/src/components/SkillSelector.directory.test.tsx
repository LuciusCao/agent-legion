import { act, fireEvent, screen, waitFor } from '@testing-library/react'
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

// 目录回显与输入跟随（自 SkillSelector.test.tsx 拆出，codex 四轮 P1 on
// #427）：绑定 key → 目录名派生（workspace 余段 / group 全段）、检查器
// 切换节点时输入跟随绑定、以及未绑定节点的待选目录跨节点不残留。

describe('SkillSelector directory echo', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSettings.mockResolvedValue(settingsWithRoot('~/.agents/skills'))
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: [],
    })
    mockGetSkillDetail.mockResolvedValue(defaultSkillDetail())
  })

  it('shows the bound skill directory in the input when echoing an existing binding (codex r2 P1 on #427)', async () => {
    // 打开已有绑定：目录输入回显 key 余段（首段 workspaceId 与只读前缀
    // 重复，codex 三轮 P2 on #427）——nested 形态同理保留后续全段。
    renderSelector({ value: 'ws-1/question_analysis/generate_key_info' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('question_analysis/generate_key_info')
  })

  it('echoes a group-form key as the full path under the skills root (independent review P3-3 on #427)', async () => {
    // demo workflow 的 group 形态 key（YAML 手写/历史数据，首段不是
    // workspaceId）：回显必须保留全段，剥掉首段会显示成 write-script、
    // 与 workspace 前缀拼出的路径不再是原绑定路径。原绑定 key 直接点
    // 「校验」：拼出的路径 = 前缀 + 全段，等于原绑定路径（key 不变形）。
    renderSelector({
      value: 'education-video-problems-generation/write-script',
    })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue(
      'education-video-problems-generation/write-script'
    )
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/education-video-problems-generation/write-script'
      )
    )
  })

  it('echoes only the remaining directory when the key starts with the current workspace (codex r3 P2 on #427)', async () => {
    // 常规 workspace 绑定 ws-1/write-script：回显 write-script（首段与
    // 只读前缀 ~/.agents/skills/ws-1/ 重复，回显全段会让用户点「校验」
    // 拼出 ws-1/ws-1/write-script，把有效绑定报成目录不存在）。原绑定
    // 回显后直接点「校验」：拼出的 key 仍等于原绑定 key、skill_key 回填
    // 原 key（validator 以技能根为 base_dir，路径解析回同一目录）。
    mockValidate.mockResolvedValueOnce({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ws-1/write-script',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    const onChange = vi.fn()
    renderSelector({ value: 'ws-1/write-script', onChange })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('write-script')
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() =>
      expect(mockValidate).toHaveBeenCalledWith(
        '~/.agents/skills/ws-1/write-script'
      )
    )
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith('ws-1/write-script')
    )
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
    // （codex 三轮 P2 on #427：首段 workspaceId 与只读前缀重复，回显
    // 余段 skill-b——与前缀拼回即原绑定路径）。
    view.rerenderWith({ value: 'ws-1/skill-b' })
    expect(input).toHaveValue('skill-b')
    // 等回显查询落地，避免其解析落在 act 外告警。
    await waitFor(() =>
      expect(mockGetSkillDetail).toHaveBeenCalledWith('ws-1/skill-b')
    )

    // 此时点「校验」校验的是 B 的目录，不会再把 A 绑上去（输入与前缀
    // 拼出 = 原绑定路径，不再拼出 ws-1/ws-1 双段）。
    mockValidate.mockClear()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '校验' }))
    })
    expect(mockValidate).toHaveBeenCalledWith('~/.agents/skills/ws-1/skill-b')
  })

  it('keeps user edits in the input while the binding stays unchanged', async () => {
    // 绑定未变（普通编辑场景）：本地输入不被外部同名 props 重置打断。
    renderSelector({ value: 'ws-1/skill-a' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    expect(input).toHaveValue('skill-a')
    fireEvent.change(input, { target: { value: 'skill-a-custom' } })
    expect(input).toHaveValue('skill-a-custom')
  })

  it('clears an unvalidated draft directory when switching to another unbound node (codex r4 P1 on #427)', async () => {
    // 节点 A、B 都未绑定：切换节点 props.name 不变，仅按 name 同步无法
    // 清掉 A 的待选目录——用户在 B 上点「校验」会把 A 的目录绑到 B。
    // nodeKey 入 SkillDirectoryInput 的重置条件后，切换即回空（B 自己的
    // 未绑定回显值）。
    const view = renderSelector({ value: '', nodeKey: 'node-a' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'xxx' } })
    expect(input).toHaveValue('xxx')

    // 切换到未绑定的节点 B：输入回到 B 的回显值（空），不是 A 的 'xxx'。
    view.rerenderWith({ nodeKey: 'node-b', value: '' })
    expect(input).toHaveValue('')

    // 在 B 上空输入点不了「校验」（按钮禁用），A 的 'xxx' 无法绑到 B。
    expect(screen.getByRole('button', { name: '校验' })).toBeDisabled()
  })
})
