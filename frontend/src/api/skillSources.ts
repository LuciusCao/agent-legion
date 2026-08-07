import { api } from './core'
import type { components } from '../generated/api'

export type SkillSourcesResponse = components['schemas']['SkillSourcesResponse']
export type SkillSourceEntry = components['schemas']['SkillSourceEntry']
export type SkillSourceUpdate = components['schemas']['SkillSourceUpdate']

const SKILL_SOURCES_URL = '/api/admin/skill-sources'

export async function getSkillSources(): Promise<SkillSourcesResponse> {
  return api<SkillSourcesResponse>(SKILL_SOURCES_URL)
}

export async function updateSkillSource(
  skillKey: string,
  input: SkillSourceUpdate
): Promise<SkillSourcesResponse> {
  // skill keys are `<workflow>/<capability>`; the route uses a :path
  // converter, so the slash stays unencoded.
  return api<SkillSourcesResponse>(`${SKILL_SOURCES_URL}/${skillKey}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function relockSkillSources(): Promise<SkillSourcesResponse> {
  return api<SkillSourcesResponse>(`${SKILL_SOURCES_URL}/relock`, {
    method: 'POST',
  })
}
