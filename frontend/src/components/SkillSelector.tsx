import { useLayoutEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSkillDetail } from '../api/agentCatalogApi'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { SkillDirectoryInput } from './SkillDirectoryInput'
import { SkillVersionSelect } from './SkillVersionSelect'
import { useSkillValidation } from './useSkillValidation'
import {
  FALLBACK_SKILLS_ROOT,
  useSkillsRootPrefix,
} from './useSkillsRootPrefix'
type Props = {
  /** 当前 workspace（workspace 技能默认目录 ~/.agents/skills/<workspaceId>/）。 */
  workspaceId: string
  /** Currently selected skill key (filled by a successful validation). */
  value: string
  onChange: (skillKey: string) => void
  /** '' / 'latest' = follow HEAD, a tag = frozen at first dispatch（#410）。 */
  skillRef: string
  onSkillRefChange: (ref: string) => void
}
/** key（如 ws-a/skill-x 或 group 形态 education-video-problems-generation/
 * write-script）→ 目录输入回显值：输入恒是「技能根下的完整相对路径」
 * （#427 二轮复审 P3-3）——validator 的 base_dir 是技能根、key 即根下
 * 全段相对路径（前端发起时由 <skills_root>/<workspaceId>/ 前缀 + 输入
 * 拼出，group 形态只会来自 YAML 手写/历史数据）；不再假设首段是
 * workspaceId 而截断 key。key 为空（未绑定）回空串。 */
function directoryNameFromKey(key: string): string {
  return key.trim().replace(/^\/+/, '')
}
/** Skill picker for the Agent editor (#410 选择链路合一)：目录名行
 * (SkillDirectoryInput) 提供候选与校验，选中即绑定；版本经
 * SkillVersionSelect 选择。tag 数据源有两路：校验响应的 tags，或回显既有
 * 绑定时经技能详情端点拉取（与 WorkflowSkillVersionSelect 同源同缓存）；
 * 已锁定版本（lock 唯一 pin）沿既有展示。校验状态机在 useSkillValidation
 * （结果按 key 归属 + 上下文作废，codex P1 on #427）。skills root 取
 * 实例设置只读字段，加载失败回退默认根并提示。 */
export function SkillSelector(props: Props) {
  const { workspaceId, value, skillRef } = props
  const { prefix, rootReady, rootLoadFailed } = useSkillsRootPrefix(workspaceId)
  const { validating, validate, invalidateInFlight, resultFor } =
    useSkillValidation(prefix, props.onChange, value)
  // 绑定上下文（节点切换/换绑）变化即作废在飞校验（codex 二轮 P1 on
  // #427）：节点 A 的迟到请求不得再触发 A 版 onChange 覆盖 B 的草稿。
  // useLayoutEffect：commit 时同步作废，不留提交窗口竞态（二轮复审 P2
  // on #427——useEffect 在 commit 后的宏任务里执行，studio 节点切换提交
  // 期间 settle 的响应恰好绕过两道保险）。作废不影响 invalid 结果展示
  // ——换绑输错时 value 未变（P2），快照归属仍命中。
  const prevValue = useRef(value)
  useLayoutEffect(() => {
    if (prevValue.current === value) return
    prevValue.current = value
    invalidateInFlight()
  }, [value, invalidateInFlight])
  // 回显绑定时的 tag 数据源：技能详情端点（不带 ref = 工作区 HEAD），
  // 与预览面板同一查询缓存（extraQueryKeys.studioSkillDetail）。
  const boundDetailQuery = useQuery({
    queryKey: extraQueryKeys.studioSkillDetail(value, null),
    queryFn: () => getSkillDetail(value),
    enabled: Boolean(value),
  })
  // 结果按 key 归属（codex P1 on #427）：节点切换后旧结果视为无结果，tags
  // 回落到当前绑定自己的详情端点；invalid 结果属于「校验发起时的绑定」
  // （同 key 才命中，独立复审 P3-1 on #427）。
  const result = resultFor(value)
  const validated = result?.valid ? result : null
  const tags: string[] = validated?.tags ?? boundDetailQuery.data?.tags ?? []
  const latestTag = tags[0] ?? null
  return (
    <div>
      <SkillDirectoryInput
        prefix={prefix}
        workspaceId={workspaceId}
        rootReady={rootReady}
        validating={validating}
        onValidate={(name) => void validate(name)}
        onEdit={invalidateInFlight}
        name={directoryNameFromKey(value)}
      />
      {rootLoadFailed && (
        <p style={{ color: '#ed6c02', fontSize: 12 }}>
          实例设置加载失败，技能根目录回退为默认 {FALLBACK_SKILLS_ROOT}。
        </p>
      )}
      {result && !result.valid && (
        <p role="alert" style={{ color: '#d32f2f', fontSize: 13 }}>
          {result.error || 'Skill 路径校验失败'}
        </p>
      )}
      <SkillVersionSelect
        value={skillRef}
        onChange={props.onSkillRefChange}
        tags={tags}
        latestTag={latestTag}
        disabled={!value}
      />
      {validated?.locked_ref && (
        <p style={{ fontSize: 12, color: '#616161', marginTop: 8 }}>
          已锁定版本：{validated.locked_ref}
        </p>
      )}
    </div>
  )
}
