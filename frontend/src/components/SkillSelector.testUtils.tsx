import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { getSkillDetail } from '../api/agentCatalogApi'
import { fetchSkillDirectories, validateSkillPath } from '../api'
import { getInstanceSettings } from '../api/instanceSettings'
import type { InstanceSettingsResponse } from '../api/instanceSettings'
import { TestQueryProvider } from '../testing/testQueryClient'
import type { SkillDetail } from '../types/agentCatalogTypes'
import { SkillSelector } from './SkillSelector'

// SkillSelector 姊妹测试文件（directory / validation，codex 四轮 P1 on
// #427 拆分）的共享脚手架：mock 注册、默认 resolved 值与 renderSelector。
// mock 工厂在各文件顶部 vi.mock 后经 vi.mocked 取同一实例；beforeEach 由
// 各文件自行为 mock 设默认 resolved 值（用例内可再覆盖）。

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

export const mockValidate = vi.mocked(validateSkillPath)
export const mockFetchDirectories = vi.mocked(fetchSkillDirectories)
export const mockGetSkillDetail = vi.mocked(getSkillDetail)
export const mockGetSettings = vi.mocked(getInstanceSettings)

export function settingsWithRoot(skillsRoot: string): InstanceSettingsResponse {
  // 用例只关心 skills_root，其余实例设置字段不参与本组件逻辑。
  return { skills_root: skillsRoot } as InstanceSettingsResponse
}

export function defaultSkillDetail(): SkillDetail {
  return {
    available: true,
    commit: 'abc123def456',
    files: [],
    key: 'ns/skill',
    ref: 'main',
    tags: [],
  }
}

type SelectorProps = Partial<{
  value: string
  nodeKey: string
  onChange: (key: string) => void
  skillRef: string
  onSkillRefChange: (ref: string) => void
}>

export function renderSelector(props: SelectorProps = {}) {
  const view = render(
    <TestQueryProvider>
      <SkillSelector
        workspaceId="ws-1"
        nodeKey={props.nodeKey ?? 'node-a'}
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
    rerenderWith: (next: SelectorProps) =>
      view.rerender(
        <TestQueryProvider>
          <SkillSelector
            workspaceId="ws-1"
            nodeKey={next.nodeKey ?? props.nodeKey ?? 'node-a'}
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
