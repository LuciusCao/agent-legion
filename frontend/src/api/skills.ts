import { api } from './core'
import type { SkillTagsResponse, SkillValidateResponse } from '../types'

export async function validateSkillPath(
  path: string
): Promise<SkillValidateResponse> {
  return api('/api/skills/validate', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

export async function fetchSkillTags(path: string): Promise<SkillTagsResponse> {
  return api(`/api/skills/tags?path=${encodeURIComponent(path)}`)
}
