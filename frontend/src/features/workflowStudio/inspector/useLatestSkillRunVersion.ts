import { useQuery } from '@tanstack/react-query'
import { fetchWorkspaceNodeRuns } from '../../../api/workspaceNodeRunsApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** #410：latest（动态跟随）绑定的实际执行版本回显——该节点最近一次 run
 * 的 node_runs.skill_version（列表 started_at 倒序，取第一条）；非 latest
 * 绑定或无 run 记录时为空字符串。
 * 按 skillKey 过滤 + 进 query key（codex 四轮 P1 on #427）：节点从
 * skill-a 换绑 skill-b 且 B 尚无运行时，按 node_key 取最近 run 会把 A 的
 * skill_version 标成 B 的「实际执行」——skill_version 是 ref@commit12，@ 前
 * 是 ref 不是 key，前端无法从版本串反推绑定，故经 schema v75 的
 * node_runs.skill 列在查询层过滤，绑定 key 变化时重新查询。B 无运行记录
 * 即无回显。 */
export function useLatestSkillRunVersion(
  workspaceId: string | undefined,
  nodeKey: string,
  skillKey: string,
  enabled: boolean
) {
  const query = useQuery({
    queryKey: extraQueryKeys.nodeRuns(workspaceId ?? '', nodeKey, skillKey),
    queryFn: () =>
      fetchWorkspaceNodeRuns(workspaceId ?? '', {
        nodeKey,
        skill: skillKey,
        limit: 1,
      }),
    enabled: enabled && Boolean(workspaceId) && Boolean(skillKey),
    select: (runs) => runs[0]?.skill_version ?? '',
  })
  return enabled ? (query.data ?? '') : ''
}
