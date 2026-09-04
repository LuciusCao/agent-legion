import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'
import { sameContext } from './skillValidationContext'
import type { SkillValidationContext } from './skillValidationContext'

/** 校验结果 + 其归属（codex P1 on #427）：检查器切换节点时 SkillSelector
 * 不卸载，仅 props.value 变化——结果必须与 key 关联使用，否则节点 A 的
 * tags 会冒充节点 B 的版本选项。invalid 的 skillKey 也记发起时的 value
 * 快照（独立复审 P2→P3-1 on #427）：输入错误属于「那次输入」发生时的绑定
 * 上下文，仅在当前绑定仍是该 value 时展示（未绑定时同为 ''，按值命中），
 * 不再按值匹配跨节点泄漏。 */
type KeyedValidationResult = {
  /** valid 结果 = 校验回填的 skill key；invalid/错误结果 = 发起校验时的
   * 绑定 value 快照（可能为 ''：首次绑定输错——空串与未绑定 value 按
   * 值命中，是独立复审 P3-1 保留的语义边界）。 */
  skillKey: string
  result: SkillValidateResponse
}
type KeyedResultState = KeyedValidationResult | null
/** Skill 校验流（自 SkillSelector 拆出，文件预算）。行为约定：
 * - 连续校验在飞时用单调序号丢弃过期响应（codex P1 on #336）；
 * - 输入继续编辑即作废在飞校验（codex P1 on #341，宿主经 onEdit 接线）；
 * - 结果按 skill key 归属（codex P1 on #427，resultFor）；
 * - 绑定上下文（value + nodeKey）变化作废在飞校验（codex 二轮 P1 /
 *   三轮 P1 on #427）：节点 A 的迟到请求不得再触发 A 版 onChange（其回写
 *   基于发起时的旧草稿 YAML）。nodeKey 入上下文后 value 不变（A、B 都
 *   未绑定或恰好同 key）的节点切换同样作废；双保险：响应应用前还比对
 *   当前上下文与发起时的上下文，不一致即丢弃（绑定 key 的 latest-ref 于
 *   render 期同步，二轮复审 P2 on #427）；onChange 走 latest-ref 调用，
 *   同一节点内等待期间的其他字段编辑不被旧闭包覆盖（codex 四轮 P1 on
 *   #427）。 */
export function useSkillValidation(
  prefix: string,
  onChange: (skillKey: string) => void,
  context: SkillValidationContext
) {
  const [validating, setValidating] = useState(false)
  const [keyedResult, setKeyedResult] = useState<KeyedResultState>(null)
  const seqRef = useRef(0)
  const inFlightRef = useRef(0)
  // latest-ref（与 useInFlightSend 同款）：绑定上下文经 useLayoutEffect
  // 同步进 ref——commit 时同步生效（useEffect 在 commit 后的宏任务里执行，
  // 节点切换提交期间 studio 的 YAML 解析可达数 ms，其间 settle 的迟到响应
  // 读到的仍是旧绑定，双保险同时失效，二轮复审 P2 on #427）；异步回调读到
  // 的恒是最新值而非闭包快照。（render 期直接写 ref 是另一可行方案，但被
  // 本仓 lint 的 react-hooks/refs 规则禁止。）
  const boundKeyRef = useRef(context)
  useLayoutEffect(() => void (boundKeyRef.current = context))
  // onChange 同样 ref 化（codex 四轮 P1 on #427）：stale() 只比对绑定上下文
  // （value + nodeKey），同一节点内等待期间的其他字段编辑不改变上下文，
  // 但 useCallback 闭包捕获的是请求发起时宿主传入的旧 onChange——其
  // patch 基于旧 definitionYaml 生成整份 YAML，会覆盖等待期间的编辑。
  // 经 ref 在响应落地时取最新回调，patch 即基于最新草稿。
  const onChangeRef = useRef(onChange)
  useLayoutEffect(() => void (onChangeRef.current = onChange))
  const invalidateInFlight = useCallback(() => ++seqRef.current, [])
  const validate = useCallback(
    async (rawName: string) => {
      const relative = rawName.trim().replace(/^\/+/, '')
      if (!relative) return
      const seq = ++seqRef.current
      const origin = boundKeyRef.current
      const originKey = origin.value
      const fullPath = `${prefix}${relative}`
      inFlightRef.current += 1
      setValidating(true)
      // 过期（更新的校验在飞，seq 变化）或绑定上下文已变（codex 二轮 P1
      // / 三轮 P1 on #427）即丢弃；catch 分支的错误结果同样随上下文丢弃。
      const stale = () =>
        seq !== seqRef.current || !sameContext(boundKeyRef.current, origin)
      try {
        const next = await validateSkillPath(fullPath)
        if (stale()) return
        setKeyedResult({
          skillKey: next.valid ? (next.skill_key ?? '') : originKey,
          result: next,
        })
        if (next.valid && next.skill_key) onChangeRef.current(next.skill_key)
      } catch (err) {
        if (stale()) return
        const error = err instanceof Error ? err.message : String(err)
        setKeyedResult({
          skillKey: originKey,
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
    [prefix]
  )
  /** 校验结果按当前绑定 key 取用：key 不匹配（含切换到另一节点）即视为
   * 无结果；invalid 结果仅在当前绑定仍等于校验发起时的绑定（换绑输错但
   * key 未变，或同为未绑定）时有效（独立复审 P3-1 on #427，按 key 归属
   * 取代按值匹配，输入错误不再跨「同绑定值」节点泄漏——nodeKey 不参与
   * 归属：同 key 节点间共享错误展示是既有语义边界）。 */
  const resultFor = useCallback(
    (value: string): SkillValidateResponse | null => {
      const r = keyedResult
      return r != null && r.skillKey === value ? r.result : null
    },
    [keyedResult]
  )
  return { validating, validate, invalidateInFlight, resultFor }
}
