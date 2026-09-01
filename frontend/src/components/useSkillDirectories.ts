import { useQuery } from '@tanstack/react-query'
import { fetchSkillDirectories } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/** workspace 技能目录候选（`<skills_root>/<workspaceId>/` 下的目录名）：
 * SkillSelector datalist 数据源（#327）。拉取失败回退空列表——手打 +
 * 「校验」按钮路径不受影响。 */
export function useSkillDirectories(workspaceId: string): string[] {
  const query = useQuery({
    queryKey: extraQueryKeys.skillDirectories(workspaceId),
    queryFn: () => fetchSkillDirectories(workspaceId),
  })
  return query.data?.directories ?? []
}
