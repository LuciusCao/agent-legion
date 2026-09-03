import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { validateSkillPath } from '../api'
import { getSkillDetail } from '../api/agentCatalogApi'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import type { SkillValidateResponse } from '../types'
import { SkillDirectoryInput } from './SkillDirectoryInput'
import { SkillVersionSelect } from './SkillVersionSelect'
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
  /** Selected version: '' / 'latest' = follow HEAD, a tag = frozen at first
   * dispatch（#410：参考 tag 下拉与 Skill ref 输入合并为一个「版本」下拉）。 */
  skillRef: string
  onSkillRefChange: (ref: string) => void
}

/** Skill picker for the Agent editor (#410 选择链路合一)：目录名行
 * (SkillDirectoryInput) 提供候选与校验，选中即绑定——上方不再另设只读
 * Skill 回显字段；版本经 SkillVersionSelect 选择。tag 数据源有两路：校验
 * 响应的 tags，或回显既有绑定时经技能详情端点拉取（与
 * WorkflowSkillVersionSelect 同源同缓存）；已锁定版本（lock 唯一 pin）沿
 * 既有展示。The skills root comes from the read-only `skills_root` field of
 * GET /api/admin/instance-settings; on load failure it falls back to the
 * default root with a hint. The validator expands `~` server-side, so the
 * composed path is sent with the tilde prefix as-is. Consecutive picks can
 * overlap in flight: a monotonic sequence number makes every response after a
 * newer request a no-op. */
export function SkillSelector(props: Props) {
  const { prefix, rootReady, rootLoadFailed } = useSkillsRootPrefix(
    props.workspaceId
  )
  const [validating, setValidating] = useState(false)
  const [result, setResult] = useState<SkillValidateResponse | null>(null)
  const validateSeq = useRef(0)

  // 回显既有绑定时版本下拉的 tag 数据源：技能详情端点（不带 ref = 工作区
  // HEAD）与预览面板同一查询缓存（extraQueryKeys.studioSkillDetail）。
  const boundDetailQuery = useQuery({
    queryKey: extraQueryKeys.studioSkillDetail(props.value, null),
    queryFn: () => getSkillDetail(props.value),
    enabled: Boolean(props.value),
  })

  async function handleValidate(rawName: string) {
    const relative = rawName.trim().replace(/^\/+/, '')
    if (!relative) return
    const seq = ++validateSeq.current
    const fullPath = `${prefix}${relative}`
    setValidating(true)
    try {
      const next = await validateSkillPath(fullPath)
      // 已有更新的校验在飞：丢弃过期响应，不覆盖最终结果（codex P1 on #336）。
      if (seq !== validateSeq.current) return
      setResult(next)
      if (next.valid && next.skill_key) props.onChange(next.skill_key)
    } catch (err) {
      if (seq !== validateSeq.current) return
      const message = err instanceof Error ? err.message : String(err)
      setResult({ valid: false, path: fullPath, error: message })
    } finally {
      if (seq === validateSeq.current) setValidating(false)
    }
  }

  // 校验响应优先（刚选定的 skill），回显绑定回落详情端点的 tags。两路的
  // latest_tag 语义一致（tags 版本倒序，首项即最新）。
  const validated = result?.valid ? result : null
  const tags = validated
    ? (validated.tags ?? [])
    : (boundDetailQuery.data?.tags ?? [])
  const latestTag: string | null = tags[0] ?? null

  return (
    <div>
      <SkillDirectoryInput
        prefix={prefix}
        workspaceId={props.workspaceId}
        rootReady={rootReady}
        validating={validating}
        onValidate={(name) => void handleValidate(name)}
        // 输入一旦变化，在飞的校验结果即过期（codex P1 on #341）。
        onEdit={() => {
          validateSeq.current += 1
        }}
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
        value={props.skillRef}
        onChange={props.onSkillRefChange}
        tags={tags}
        latestTag={latestTag}
        disabled={!props.value}
      />
      {validated?.locked_ref && (
        <p style={{ fontSize: 12, color: '#616161', marginTop: 8 }}>
          已锁定版本：{validated.locked_ref}
        </p>
      )}
    </div>
  )
}
