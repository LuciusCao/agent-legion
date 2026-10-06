import { describe, expect, it } from 'vitest'
import type {
  QualityReplay,
  QualityReplayProfileOption,
} from '../../api/qualityApi'
import {
  DRAFT_CHOICE,
  ORIGINAL_CHOICE,
  createBody,
  optionLabel,
  originalChoiceLabel,
  optionValue,
  originalLabel,
  replayLabel,
} from './qualityReplayProfiles'

const revision: QualityReplayProfileOption = {
  source: 'revision',
  revision_id: 'rev-7',
  revision_version: 7,
  revision_status: 'active',
  is_original: true,
  runtime: 'velites',
  provider: 'p',
  model: 'm',
  profile_hash: 'h',
}

function replay(overrides: Partial<QualityReplay>): QualityReplay {
  return {
    id: 'r1',
    item_id: 'i1',
    agent_id: '',
    agent_version: null,
    revision_id: null,
    revision_version: null,
    profile_hash: '',
    replay_job_id: '',
    status: 'pending',
    error_message: '',
    created_by: '',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as QualityReplay
}

describe('qualityReplayProfiles (#1079 D6)', () => {
  it('maps the select choice to the create request', () => {
    expect(createBody(ORIGINAL_CHOICE)).toEqual({ use_draft: false })
    expect(createBody(DRAFT_CHOICE)).toEqual({ use_draft: true })
    expect(createBody(optionValue(revision))).toEqual({
      revision_id: 'rev-7',
      use_draft: false,
    })
  })

  it('labels options with version, runtime/model and the original marker', () => {
    expect(optionLabel(revision)).toBe(
      'v7（当前生效） · velites / m · 与原运行一致'
    )
    expect(
      optionLabel({ ...revision, source: 'draft', runtime: 'pi', model: '' })
    ).toBe('当前草稿 · pi · 与原运行一致')
  })

  it('labels replays by their frozen profile source', () => {
    expect(
      replayLabel(
        replay({ revision_id: 'x', revision_version: 3, profile_hash: 'h' })
      )
    ).toBe('revision v3')
    expect(replayLabel(replay({ profile_hash: 'h' }))).toBe('草稿')
    expect(replayLabel(replay({ agent_version: 5 }))).toBe('Agent v5')
    expect(replayLabel(replay({}))).toBe('原执行档案')
    expect(originalLabel(3)).toBe('Agent v3')
    expect(originalLabel(null)).toBe('原执行档案')
    expect(originalChoiceLabel(3)).toBe('原运行的执行档案（Agent v3）')
    expect(originalChoiceLabel(null)).toBe('原运行的执行档案')
  })
})
