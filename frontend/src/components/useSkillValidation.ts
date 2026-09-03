import { useCallback, useEffect, useRef, useState } from 'react'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'

/** 校验结果 + 其归属（codex P1 on #427）：检查器切换节点时 SkillSelector
 * 不卸载，仅 props.value 变化——结果必须与 key 关联使用，否则节点 A 的
 * tags 会冒充节点 B 的版本选项。invalid 结果 key 为 null，归属改为校验
 * 发起时的 value 快照（独立复审 P2 on #427）：value 为空（首次绑定输错）
 * 或等于快照（已绑定节点换绑输错，key 未变）时展示，换绑错误不再零反馈。 */
type KeyedValidationResult = {
  skillKey: string | null
  /** invalid 结果校验发起时的绑定 value；valid 结果恒为 null（key 即归属）。 */
  invalidBoundValue: string | null
  result: SkillValidateResponse
}
type KeyedResultState = KeyedValidationResult | null
/** Skill 校验流（自 SkillSelector 拆出，文件预算）。行为约定：
 * - 连续校验在飞时用单调序号丢弃过期响应（codex P1 on #336）；
 * - 输入继续编辑即作废在飞校验（codex P1 on #341，宿主经 onEdit 接线）；
 * - 结果按 skill key 归属（codex P1 on #427，resultFor）；
 * - 绑定上下文变化作废在飞校验（codex 二轮 P1 on #427）：节点 A 的迟到
 *   请求不得再触发 A 版 onChange（其回写基于发起时的旧草稿 YAML）；双
 *   保险：响应应用前还比对当前绑定与发起时的绑定，不一致即丢弃。 */
export function useSkillValidation(
  prefix: string,
  onChange: (skillKey: string) => void,
  boundKey: string
) {
  const [validating, setValidating] = useState(false)
  const [keyedResult, setKeyedResult] = useState<KeyedResultState>(null)
  const seqRef = useRef(0)
  const inFlightRef = useRef(0)
  // latest-ref（与 useInFlightSend 同款）：绑定 key 经 effect 同步进 ref，
  // 异步回调读到的是最新值而非闭包快照。
  const boundKeyRef = useRef(boundKey)
  useEffect(() => void (boundKeyRef.current = boundKey))
  const invalidateInFlight = useCallback(() => ++seqRef.current, [])
  const validate = useCallback(
    async (rawName: string) => {
      const relative = rawName.trim().replace(/^\/+/, '')
      if (!relative) return
      const seq = ++seqRef.current
      const origin = boundKeyRef.current
      const fullPath = `${prefix}${relative}`
      inFlightRef.current += 1
      setValidating(true)
      // 过期（更新的校验在飞）或绑定上下文已变（codex 二轮 P1）即丢弃；
      // 例外：当前绑定恰为本响应的 key（宿主已回显本次回填、或切到绑定
      // 同一 skill 的节点——结果本就属于该 key）仍应用；catch 分支传
      // null——错误结果不属于任何 key，上下文变化即丢弃。
      const stale = (k: string | null) =>
        seq !== seqRef.current ||
        (boundKeyRef.current !== origin && boundKeyRef.current !== k)
      try {
        const next = await validateSkillPath(fullPath)
        const k = next.skill_key ?? null
        if (stale(k)) return
        const b = next.valid ? null : origin
        setKeyedResult({ skillKey: k, invalidBoundValue: b, result: next })
        if (next.valid && next.skill_key) onChange(next.skill_key)
      } catch (err) {
        if (stale(null)) return
        const error = err instanceof Error ? err.message : String(err)
        setKeyedResult({
          skillKey: null,
          invalidBoundValue: origin,
          result: { valid: false, path: fullPath, error },
        })
      } finally {
        // loading 按在飞计数收敛（而非序号）：被作废的请求返回时序号已
        // 过期，但没有更新的请求在飞时按钮必须复原，否则会卡死在
        // 「校验中...」。
        inFlightRef.current -= 1
        if (inFlightRef.current <= 0) setValidating(false)
      }
    },
    [prefix, onChange]
  )
  /** 校验结果按当前绑定 key 取用：key 不匹配（含切换到另一节点）即视为
   * 无结果；invalid 结果（key 为 null）在未绑定或绑定仍是校验发起时的值
   * （换绑输错，独立复审 P2）时有效。 */
  const resultFor = useCallback(
    (value: string): SkillValidateResponse | null => {
      const r = keyedResult
      if (r == null) return null
      if (r.skillKey != null) return r.skillKey === value ? r.result : null
      return value === '' || value === r.invalidBoundValue ? r.result : null
    },
    [keyedResult]
  )
  return { validating, validate, invalidateInFlight, resultFor }
}
