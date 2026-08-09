import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { SkillSourcesSection } from './SkillSourcesSection'
import {
  getSkillSources,
  relockSkillSources,
  updateSkillSource,
} from '../../api/skillSources'
import type { SkillSourcesResponse } from '../../api/skillSources'

vi.mock('../../api/skillSources', () => ({
  getSkillSources: vi.fn(),
  updateSkillSource: vi.fn(),
  relockSkillSources: vi.fn(),
}))

const sources: SkillSourcesResponse = {
  skills: [
    {
      key: 'video_knowledge/generate_chapters',
      repo: '~/.agents/skills/agent-legion/video_knowledge/generate_chapters',
      ref: 'v1.0.2',
      locked_commit: '957768e8e0e0ed731f3e07ac0111f961d8f42ae9',
      resolved_at: '2026-08-07T01:44:10Z',
      stale: false,
    },
    {
      key: 'question_comprehension_info/generate_key_info',
      repo: '~/.agents/skills/agent-legion/question_comprehension_info/generate_key_info',
      ref: 'v9.9.9',
      locked_commit: '42356b845038780016d28e49a9e99bea1c685ec0',
      resolved_at: '2026-08-07T01:44:10Z',
      stale: true,
    },
  ],
}

function renderSection() {
  return render(
    <MemoryRouter>
      <SkillSourcesSection />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getSkillSources).mockResolvedValue(sources)
})

describe('SkillSourcesSection', () => {
  it('renders the merged source list with truncated commits and stale badges', async () => {
    renderSection()

    expect(
      await screen.findByText('video_knowledge/generate_chapters')
    ).toBeInTheDocument()
    expect(
      screen.getByText('question_comprehension_info/generate_key_info')
    ).toBeInTheDocument()
    // Locked commits render truncated to 8 chars.
    expect(screen.getByText('957768e8')).toBeInTheDocument()
    expect(screen.getByText('42356b84')).toBeInTheDocument()
    // Exactly the one stale entry carries the badge.
    expect(screen.getAllByText('stale')).toHaveLength(1)
    expect(
      screen.getByText(/skill 源与锁存于数据库；修改 ref 后需刷新锁解析 commit/)
    ).toBeInTheDocument()
  })

  it('saves an edited ref via PUT and applies the returned view', async () => {
    vi.mocked(updateSkillSource).mockImplementation(async (key, input) => ({
      skills: sources.skills.map((skill) =>
        skill.key === key ? { ...skill, ...input, stale: true } : skill
      ),
    }))

    renderSection()
    await screen.findByText('video_knowledge/generate_chapters')

    fireEvent.click(
      screen.getByLabelText('编辑 video_knowledge/generate_chapters')
    )
    fireEvent.change(
      screen.getByLabelText('video_knowledge/generate_chapters ref'),
      { target: { value: 'v1.0.3' } }
    )
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => {
      expect(updateSkillSource).toHaveBeenCalledWith(
        'video_knowledge/generate_chapters',
        {
          repo: '~/.agents/skills/agent-legion/video_knowledge/generate_chapters',
          ref: 'v1.0.3',
        }
      )
    })
    // The merged view returned by PUT replaces the row.
    expect(await screen.findByText('v1.0.3')).toBeInTheDocument()
    expect(screen.getAllByText('stale')).toHaveLength(2)
  })

  it('relocks via POST and applies the refreshed view', async () => {
    vi.mocked(relockSkillSources).mockResolvedValue({
      skills: sources.skills.map((skill) => ({ ...skill, stale: false })),
    })

    renderSection()
    await screen.findByText('video_knowledge/generate_chapters')

    fireEvent.click(screen.getByText('刷新锁'))

    await waitFor(() => {
      expect(relockSkillSources).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(screen.queryByText('stale')).not.toBeInTheDocument()
    })
  })

  it('shows the server error when relock fails', async () => {
    vi.mocked(relockSkillSources).mockRejectedValue(
      new Error('HTTP 500: git failed')
    )

    renderSection()
    await screen.findByText('video_knowledge/generate_chapters')

    fireEvent.click(screen.getByText('刷新锁'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'HTTP 500: git failed'
    )
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getSkillSources).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
