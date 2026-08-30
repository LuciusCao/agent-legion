import { useQuery } from '@tanstack/react-query'
import { getInstanceSettings } from '../api/instanceSettings'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/** 实例设置加载失败时的兜底技能根（与后端 skill_roots.SKILLS_ROOT_DISPLAY 一致）。 */
export const FALLBACK_SKILLS_ROOT = '~/.agents/skills'

/** workspace 技能根前缀（`<skills_root>/<workspaceId>/`）：skills_root 取实例
 * 设置的只读字段（单一来源后端 skill_roots.py，与全局设置页共享 query 缓存）。
 * 加载完成前 rootReady=false（调用方可先禁用输入）；加载失败回退默认根并置
 * rootLoadFailed，由调用方决定提示文案。 */
export function useSkillsRootPrefix(workspaceId: string): {
  prefix: string
  rootReady: boolean
  rootLoadFailed: boolean
} {
  const settingsQuery = useQuery({
    queryKey: extraQueryKeys.instanceSettings(),
    queryFn: getInstanceSettings,
  })
  const skillsRoot = (
    settingsQuery.data?.skills_root ?? FALLBACK_SKILLS_ROOT
  ).replace(/\/+$/, '')
  return {
    prefix: `${skillsRoot}/${workspaceId}/`,
    rootReady: !settingsQuery.isPending,
    rootLoadFailed: settingsQuery.isError,
  }
}
