import { useLayoutEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSkillDetail } from '../api/agentCatalogApi'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { SkillDirectoryInput } from './SkillDirectoryInput'
import { SkillVersionSelect } from './SkillVersionSelect'
import { directoryNameFromKey } from './skillDirectoryName'
import { sameContext } from './skillValidationContext'
import { useSkillValidation } from './useSkillValidation'
import {
  FALLBACK_SKILLS_ROOT,
  useSkillsRootPrefix,
} from './useSkillsRootPrefix'
type Props = {
  /** 当前 workspace（技能默认目录 ~/.agents/skills/<workspaceId>/）；
   * nodeKey：发起校验的检查器节点身份，入校验上下文——A、B 都未绑定
   * （value 同为空串）时切换节点仍作废在飞校验（codex 三轮 P1 on #427）。 */
  workspaceId: string
  nodeKey: string
  /** Currently selected skill key (filled by a successful validation). */
  value: string
  onChange: (skillKey: string) => void
  /** '' / 'latest' = follow HEAD, a tag = frozen at first dispatch（#410）。 */
  skillRef: string
  onSkillRefChange: (ref: string) => void
}
/** Skill picker for the Agent editor (#410 选择链路合一)：目录名行
 * (SkillDirectoryInput) 提供候选与校验，选中即绑定；版本经
 * SkillVersionSelect 选择。tag 数据源有两路：校验响应的 tags，或回显既有
 * 绑定时经技能详情端点拉取（与 WorkflowSkillVersionSelect 同源同缓存）；
 * 已锁定版本（lock 唯一 pin）沿既有展示。校验状态机在 useSkillValidation
 * （结果按 key 归属 + 按 value/nodeKey 上下文作废，codex P1 on #427）。
 * skills root 取实例设置只读字段，加载失败回退默认根并提示。 */
export function SkillSelector(props: Props) {
  const { workspaceId, nodeKey, value, skillRef } = props
  const { prefix, rootReady, rootLoadFailed } = useSkillsRootPrefix(workspaceId)
  // 校验上下文 = 绑定 key + 节点身份（codex 三轮 P1 on #427）。
  const { validating, validate, invalidateInFlight, resultFor } =
    useSkillValidation(prefix, props.onChange, { value, nodeKey })
  // 绑定上下文（节点切换/换绑）变化即作废在飞校验（codex 二轮 P1 /
  // 三轮 P1 on #427）：节点 A 的迟到请求不得再触发 A 版 onChange 覆盖 B
  // 的草稿。上下文含 nodeKey——A、B 都未绑定（value 都为空串）时切换节点
  // 也作废。useLayoutEffect：commit 时同步作废，不留提交窗口竞态（二轮
  // 复审 P2 on #427——useEffect 在 commit 后的宏任务里执行，studio 节点
  // 切换提交期间 settle 的响应恰好绕过两道保险）。作废不影响 invalid
  // 结果展示——换绑输错时 value 未变（P2），快照归属仍命中。
  const prevContext = useRef({ value, nodeKey })
  useLayoutEffect(() => {
    if (sameContext(prevContext.current, { value, nodeKey })) return
    prevContext.current = { value, nodeKey }
    invalidateInFlight()
  }, [value, nodeKey, invalidateInFlight])
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
  return (
    <div>
      <SkillDirectoryInput
        prefix={prefix}
        workspaceId={workspaceId}
        nodeKey={nodeKey}
        rootReady={rootReady}
        validating={validating}
        onValidate={(name) => void validate(name)}
        onEdit={invalidateInFlight}
        name={directoryNameFromKey(value, workspaceId)}
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
        latestTag={tags[0] ?? null}
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
