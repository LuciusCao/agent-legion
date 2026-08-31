import { api } from './core'
import type {
  SkillDirectoriesResponse,
  SkillTagsResponse,
  SkillValidateResponse,
} from '../types'

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

export async function fetchSkillDirectories(
  scope: string
): Promise<SkillDirectoriesResponse> {
  return api(`/api/skills/directories?scope=${encodeURIComponent(scope)}`)
}
