import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

/** 归一化节点 skill 绑定（#322：空 ref 与 latest 同义 = 跟随仓库 HEAD）。
 * 与后端 loader（workflow_node_skill.load_node_skill）同一归一：ref 恒非空。 */
export function normalizeNodeSkill(
  skill: WorkflowYamlNode['skill'] | null | undefined
): { key: string; ref: string } | null {
  if (!skill) return null
  if (typeof skill === 'string') {
    return skill.trim() ? { key: skill.trim(), ref: 'latest' } : null
  }
  const key = (skill.key ?? '').trim()
  if (!key) return null
  return { key, ref: (skill.ref ?? '').trim() || 'latest' }
}

// #76：节点级 skill 绑定（仅对 Agent 路由节点有意义）。#322：ref 空归一为
// latest（跟随仓库 HEAD，不冻结），echo 恒为 mapping 形态（对齐后端
// apply_skill_echo）；具体 tag = 首次 dispatch 冻结进 skill_lock。null 移除绑定。
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
    node.skill = { key, ref: skill.ref.trim() || 'latest' }
  }
  return dumpWorkflowYaml(draft)
}
