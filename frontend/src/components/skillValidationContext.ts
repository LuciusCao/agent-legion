/** Skill 校验的绑定上下文（自 useSkillValidation.ts 拆出，文件预算，
 * codex 四轮 P1 on #427）：value（当前绑定 key）+ nodeKey（发起校验的
 * 检查器节点身份，宿主传入）。value 相同而节点不同（A、B 都未绑定）时
 * 仍是不同上下文（codex 三轮 P1 on #427）。 */
export type SkillValidationContext = { value: string; nodeKey: string }

/** 比较绑定上下文是否变化（key 快照只按 value 归属展示；宿主复用其收窄
 * useLayoutEffect 依赖，codex 三轮 P1 on #427）。 */
export const sameContext = (
  a: SkillValidationContext,
  b: SkillValidationContext
) => a.value === b.value && a.nodeKey === b.nodeKey
