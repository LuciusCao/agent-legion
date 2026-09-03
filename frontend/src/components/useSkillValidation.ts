import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'

/** 校验结果 + 其归属（codex P1 on #427）：检查器切换节点时 SkillSelector
 * 不卸载，仅 props.value 变化——结果必须与 key 关联使用，否则节点 A 的
 * tags 会冒充节点 B 的版本选项。invalid 的 skillKey 也记发起时的 value
 * 快照（独立复审 P2→P3-1 on #427）：输入错误属于「那次输入」发生时的绑定
 * 上下文，仅在当前绑定仍是该 key 时展示（未绑定时 key 为 null，null===
 * null 命中），不再按值匹配跨节点泄漏。 */
type KeyedValidationResult = {
  /** valid 结果 = 校验回填的 skill key；invalid/错误结果 = 发起校验时的
   * 绑定 value（可能为 null：首次绑定输错）。 */
  skillKey: string | null
  result: SkillValidateResponse
}
type KeyedResultState = KeyedValidationResult | null
/** Skill 校验流（自 SkillSelector 拆出，文件预算）。行为约定：
 * - 连续校验在飞时用单调序号丢弃过期响应（codex P1 on #336）；
 * - 输入继续编辑即作废在飞校验（codex P1 on #341，宿主经 onEdit 接线）；
 * - 结果按 skill key 归属（codex P1 on #427，resultFor）；
 * - 绑定上下文变化作废在飞校验（codex 二轮 P1 on #427）：节点 A 的迟到
 *   请求不得再触发 A 版 onChange（其回写基于发起时的旧草稿 YAML）；双
 *   保险：响应应用前还比对当前绑定与发起时的绑定，不一致即丢弃（绑定
 *   key 的 latest-ref 于 render 期同步，二轮复审 P2 on #427）。 */
export function useSkillValidation(
  prefix: string,
  onChange: (skillKey: string) => void,
  boundKey: string
) {
  const [validating, setValidating] = useState(false)
  const [keyedResult, setKeyedResult] = useState<KeyedResultState>(null)
  const seqRef = useRef(0)
  const inFlightRef = useRef(0)
  // latest-ref（与 useInFlightSend 同款）：绑定 key 经 useLayoutEffect 同步
  // 进 ref——commit 时同步生效（useEffect 在 commit 后的宏任务里执行，节点
  // 切换提交期间 studio 的 YAML 解析可达数 ms，其间 settle 的迟到响应读到
  // 的仍是旧绑定，双保险同时失效，二轮复审 P2 on #427）；异步回调读到的
  // 恒是最新值而非闭包快照。（render 期直接写 ref 是另一可行方案，但被
  // 本仓 lint 的 react-hooks/refs 规则禁止。）
  const boundKeyRef = useRef(boundKey)
  useLayoutEffect(() => void (boundKeyRef.current = boundKey))
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
      // 过期（更新的校验在飞）或绑定上下文已变（codex 二轮 P1）即丢弃
      // （上下文例外分支不可达：宿主的 invalidateInFlight 已把 seq 作废，
      // 恒被序号短路先拦截，删除）；catch 分支传 null——错误结果不属于
      // 任何 key，上下文变化即丢弃。
      const stale = () =>
        seq !== seqRef.current || boundKeyRef.current !== origin
      try {
        const next = await validateSkillPath(fullPath)
        const k = next.valid ? (next.skill_key ?? null) : origin
        if (stale()) return
        setKeyedResult({ skillKey: k, result: next })
        if (next.valid && next.skill_key) onChange(next.skill_key)
      } catch (err) {
        if (stale()) return
        const error = err instanceof Error ? err.message : String(err)
        setKeyedResult({
          skillKey: origin,
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
   * 无结果；invalid 结果仅在当前绑定仍等于校验发起时的绑定（换绑输错但
   * key 未变，或同为未绑定）时有效（独立复审 P3-1 on #427，按 key 归属
   * 取代按值匹配，输入错误不再跨「同绑定值」节点泄漏）。 */
  const resultFor = useCallback(
    (value: string): SkillValidateResponse | null => {
      const r = keyedResult
      return r != null && r.skillKey === value ? r.result : null
    },
    [keyedResult]
  )
  return { validating, validate, invalidateInFlight, resultFor }
}
