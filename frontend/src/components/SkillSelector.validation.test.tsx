import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SkillValidateResponse } from '../types'
import {
  defaultSkillDetail,
  mockFetchDirectories,
  mockGetSettings,
  mockGetSkillDetail,
  mockValidate,
  renderSelector,
  settingsWithRoot,
} from './SkillSelector.testUtils'

// 异步校验与竞态（自 SkillSelector.test.tsx 拆出，codex 四轮 P1 on #427）：
// 校验结果按绑定 key 归属、在飞请求的作废路径（继续编辑/切换节点/同 key
// 节点切换）、invalid 结果的展示边界、以及响应应用走最新回调（同节点内
// 等待期间的其他字段编辑不被旧闭包覆盖）。

describe('SkillSelector validation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSettings.mockResolvedValue(settingsWithRoot('~/.agents/skills'))
    mockFetchDirectories.mockResolvedValue({
      workspace_id: 'ws-1',
      directories: [],
    })
    mockGetSkillDetail.mockResolvedValue(defaultSkillDetail())
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
    } as ReturnType<typeof defaultSkillDetail>)
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

  it('discards a late response after switching between two unbound nodes (codex r3 P1 on #427)', async () => {
    // 节点 A、B 都未绑定（value 同为空串）：切换节点不改变 value，旧的
    // 「监听绑定值」守卫失效——A 的在途响应仍可调用其捕获的旧 onChange，
    // 基于旧 definitionYaml 覆盖 B 上刚完成的草稿编辑。nodeKey 入校验
    // 上下文后，value 不变的节点切换同样作废在飞校验。
    let resolveA!: (value: SkillValidateResponse) => void
    mockValidate.mockImplementationOnce(
      () =>
        new Promise<SkillValidateResponse>((resolve) => {
          resolveA = resolve
        })
    )
    const onChange = vi.fn()
    const view = renderSelector({ onChange, value: '', nodeKey: 'node-a' })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-a' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(1))

    // 切换到节点 B（同样未绑定，value 仍为空串，只有 nodeKey 变化）。
    view.rerenderWith({ nodeKey: 'node-b', value: '' })

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
    // A 的迟到响应不得写盘（onChange 触发即 patch 旧节点草稿）、不得
    // 弹 A 的锁定版本、也不得残留 A 的 tags 进 B 的版本下拉。
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('已锁定版本：aaabbb')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '校验' })).toBeInTheDocument()
    expect(mockGetSkillDetail).not.toHaveBeenCalledWith('ws-1/skill-a')
  })

  it('discards a late response after switching between two nodes bound to the same key (codex r3 P1 on #427)', async () => {
    // A、B 恰好绑定同一个 key：切换节点 value 不变。B 上刚完成的草稿编辑
    // （换绑流程中间态）不得被 A 发起的校验回写覆盖。
    let resolveA!: (value: SkillValidateResponse) => void
    mockValidate.mockImplementationOnce(
      () =>
        new Promise<SkillValidateResponse>((resolve) => {
          resolveA = resolve
        })
    )
    const onChange = vi.fn()
    const view = renderSelector({
      onChange,
      value: 'ws-1/skill-b',
      nodeKey: 'node-a',
    })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-x' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(1))

    // 切换到节点 B（绑定同 key，value 不变，仅 nodeKey 变化）。
    view.rerenderWith({ nodeKey: 'node-b', value: 'ws-1/skill-b' })

    await act(async () => {
      resolveA({
        valid: true,
        path: '/abs/skill-x',
        skill_key: 'ws-1/skill-x',
        tags: [],
        latest_tag: null,
        locked_ref: null,
      })
    })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '校验' })).toBeInTheDocument()
  })

  it('applies the validation response through the latest onChange while the same node is edited (codex r4 P1 on #427)', async () => {
    // 同一节点内（value/nodeKey 不变，stale() 通过）：校验在飞期间用户继续
    // 编辑该节点其他字段——宿主传入新的 onChange（patch 基于最新草稿
    // YAML）。旧闭包捕获的 onChange 会基于旧 definitionYaml 生成整份 YAML
    // 覆盖等待期间的编辑；onChange ref 化后，响应落地调用的是最新回调。
    let resolveA!: (value: SkillValidateResponse) => void
    mockValidate.mockImplementationOnce(
      () =>
        new Promise<SkillValidateResponse>((resolve) => {
          resolveA = resolve
        })
    )
    const onChangeInitial = vi.fn()
    const onChangeLatest = vi.fn()
    const view = renderSelector({
      onChange: onChangeInitial,
      value: '',
      nodeKey: 'node-a',
    })

    const input = screen.getByLabelText('Skill 目录名')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: 'skill-a' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() => expect(mockValidate).toHaveBeenCalledTimes(1))

    // 等待期间：同节点其他字段编辑 → 宿主重新渲染并传入新的 onChange
    // （value/nodeKey 不变，绑定上下文校验通过，校验不作废）。
    view.rerenderWith({
      nodeKey: 'node-a',
      value: '',
      onChange: onChangeLatest,
    })

    await act(async () => {
      resolveA({
        valid: true,
        path: '/abs/skill-a',
        skill_key: 'ws-1/skill-a',
        tags: [],
        latest_tag: null,
        locked_ref: null,
      })
    })
    // 响应经最新回调应用（不是请求发起时闭包捕获的 onChangeInitial）。
    expect(onChangeLatest).toHaveBeenCalledWith('ws-1/skill-a')
    expect(onChangeInitial).not.toHaveBeenCalled()
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
    expect(input).toHaveValue('skill-b')
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
    expect(input).toHaveValue('skill-b')
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
    expect(input).toHaveValue('skill-b')
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
