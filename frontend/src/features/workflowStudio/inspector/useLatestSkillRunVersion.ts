import { useQuery } from '@tanstack/react-query'
import { fetchWorkspaceNodeRuns } from '../../../api/workspaceNodeRunsApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** #410：latest（动态跟随）绑定的实际执行版本回显——该节点最近一次 run
 * 的 node_runs.skill_version（列表 started_at 倒序，取第一条）；非 latest
 * 绑定或无 run 记录时为空字符串。 */
export function useLatestSkillRunVersion(
  workspaceId: string | undefined,
  nodeKey: string,
  enabled: boolean
) {
  const query = useQuery({
    queryKey: extraQueryKeys.nodeRuns(workspaceId ?? '', nodeKey),
    queryFn: () =>
      fetchWorkspaceNodeRuns(workspaceId ?? '', { nodeKey, limit: 1 }),
    enabled: enabled && Boolean(workspaceId),
    select: (runs) => runs[0]?.skill_version ?? '',
  })
  return enabled ? (query.data ?? '') : ''
}
