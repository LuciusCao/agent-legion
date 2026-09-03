import { useCallback, useRef, useState } from 'react'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'

/** 校验结果 + 其归属的 skill key（codex P1 on #427）：检查器切换节点时
 * SkillSelector 不卸载，仅 props.value 变化——结果必须与 key 关联使用，
 * 否则节点 A 的 tags 会冒充节点 B 的版本选项（可能把 A 的 tag 写进 B 的
 * 绑定）。invalid 结果的 key 为 null，仅未绑定任何 skill 时展示。 */
type KeyedValidationResult = {
  skillKey: string | null
  result: SkillValidateResponse
}

/** Skill 校验流（自 SkillSelector 拆出，文件预算）。行为约定：
 * - 连续校验在飞时用单调序号丢弃过期响应（codex P1 on #336）；
 * - 输入继续编辑即作废在飞校验（codex P1 on #341，宿主经 onEdit 接线）；
 * - 结果按 skill key 归属（codex P1 on #427）：resultFor(value) 只在 key
 *   匹配当前绑定（或 invalid 结果且当前未绑定）时返回，否则 null——检查器
 *   切换节点后旧结果视为无结果。 */
export function useSkillValidation(
  prefix: string,
  onChange: (skillKey: string) => void
) {
  const [validating, setValidating] = useState(false)
  const [keyedResult, setKeyedResult] = useState<KeyedValidationResult | null>(
    null
  )
  const validateSeq = useRef(0)

  const invalidateInFlight = useCallback(() => {
    validateSeq.current += 1
  }, [])

  const validate = useCallback(
    async (rawName: string) => {
      const relative = rawName.trim().replace(/^\/+/, '')
      if (!relative) return
      const seq = ++validateSeq.current
      const fullPath = `${prefix}${relative}`
      setValidating(true)
      try {
        const next = await validateSkillPath(fullPath)
        // 已有更新的校验在飞：丢弃过期响应，不覆盖最终结果。
        if (seq !== validateSeq.current) return
        setKeyedResult({ skillKey: next.skill_key ?? null, result: next })
        if (next.valid && next.skill_key) onChange(next.skill_key)
      } catch (err) {
        if (seq !== validateSeq.current) return
        const message = err instanceof Error ? err.message : String(err)
        setKeyedResult({
          skillKey: null,
          result: { valid: false, path: fullPath, error: message },
        })
      } finally {
        if (seq === validateSeq.current) setValidating(false)
      }
    },
    [prefix, onChange]
  )

  /** 校验结果按当前绑定 key 取用：key 不匹配（含切换到另一节点）即视为
   * 无结果；invalid 结果（key 为 null）只在尚未绑定 key 时有效。 */
  const resultFor = useCallback(
    (value: string): SkillValidateResponse | null => {
      if (keyedResult == null) return null
      const matches =
        keyedResult.skillKey == null
          ? value === ''
          : keyedResult.skillKey === value
      return matches ? keyedResult.result : null
    },
    [keyedResult]
  )

  return { validating, validate, invalidateInFlight, resultFor }
}
