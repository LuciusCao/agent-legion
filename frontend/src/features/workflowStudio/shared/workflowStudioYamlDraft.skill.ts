import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

/** 归一化节点 skill 绑定（字符串形态 = 仅 key，ref 回落默认）。 */
export function normalizeNodeSkill(
  skill: WorkflowYamlNode['skill'] | null | undefined
): { key: string; ref: string } | null {
  if (!skill) return null
  if (typeof skill === 'string') {
    return skill.trim() ? { key: skill.trim(), ref: '' } : null
  }
  const key = (skill.key ?? '').trim()
  return key ? { key, ref: (skill.ref ?? '').trim() } : null
}

// #76：节点级 skill 绑定（仅对 Agent 路由节点有意义）。ref 为空时落字符串
// 形态（回落 skill_sources 默认 ref），null 移除绑定。
export function patchWorkflowNodeSkill(
  rawYaml: string,
  nodeKey: string,
  skill: { key: string; ref: string } | null
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (!skill || !skill.key.trim()) {
    delete node.skill
  } else {
    const key = skill.key.trim()
    const ref = skill.ref.trim()
    node.skill = ref ? { key, ref } : key
  }
  return dumpWorkflowYaml(draft)
}
