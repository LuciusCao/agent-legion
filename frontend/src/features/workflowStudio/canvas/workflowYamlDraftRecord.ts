import type {
  WorkflowDefinitionRecord,
  WorkflowNodeRecord,
} from '../../../types'
import {
  parseWorkflowYaml,
  type WorkflowYamlNode,
  type WorkflowYamlObject,
} from '../shared/workflowStudioYamlDraft.parse'
import { normalizeNodeSkill } from '../shared/workflowStudioYamlDraft.skill'

/** 草稿 YAML → WorkflowDefinitionRecord：让画布/inspector 直接以草稿为数据
 * 源（近实时随 draftYaml 重算）。解析失败（编辑中途的非法 YAML）返回 null，
 * 由调用方回退已发布 workflow 并给出低噪提示，不得报错卡死或清空画布。
 * 字段归一化与 ghostDraftNodeDetails 一致：显式类型 start/agent/approval
 * 各自还原，遗留 `type: node` 与缺失一律按 code（同后端 loader）。 */
export function workflowYamlToDefinitionRecord(
  rawYaml: string
): WorkflowDefinitionRecord | null {
  let parsed: WorkflowYamlObject
  try {
    parsed = parseWorkflowYaml(rawYaml)
  } catch {
    return null
  }
  return {
    key: parsed.key ?? '',
    label: parsed.label ?? parsed.key ?? '',
    intake: mapIntake(parsed.intake),
    nodes: Object.entries(parsed.nodes ?? {}).map(([key, node]) =>
      mapNode(key, node)
    ),
    edges: (parsed.edges ?? [])
      .filter((edge) => edge.source && edge.target)
      .map((edge) => ({
        source: edge.source as string,
        target: edge.target as string,
        condition: edge.condition?.path
          ? {
              artifact: edge.condition.artifact ?? '',
              path: edge.condition.path,
              equals: edge.condition.equals,
            }
          : null,
      })),
  }
}

function mapNode(key: string, node: WorkflowYamlNode): WorkflowNodeRecord {
  const skill = normalizeNodeSkill(node.skill)
  return {
    key,
    label: node.label ?? key,
    capability: node.capability ?? '',
    after: node.after ?? [],
    inputs: node.inputs ?? [],
    outputs: node.outputs ?? [],
    ...(node.terminal?.outcome
      ? { terminal: { outcome: node.terminal.outcome } }
      : {}),
    ...(node.execution
      ? {
          execution: {
            provider: node.execution.provider ?? '',
            model: node.execution.model ?? '',
            thinking: node.execution.thinking ?? '',
            prompt: node.execution.prompt ?? '',
          },
        }
      : {}),
    ...(skill ? { skill } : {}),
    ...(node.type === 'start'
      ? {
          node_type: 'start',
          accepted_item_types: node.accepted_item_types ?? [],
        }
      : {
          node_type:
            node.type === 'agent'
              ? 'agent'
              : node.type === 'approval'
                ? 'approval'
                : 'code',
        }),
  }
}

// YAML 的 intake.modes 是 mapping（key → {label, input_field}）；response 是
// 数组。画布不消费 intake，这里只做类型完备的防御性转换。
function mapIntake(intake: unknown): WorkflowDefinitionRecord['intake'] {
  const modes = (intake as { modes?: unknown } | undefined)?.modes
  if (!modes || typeof modes !== 'object' || Array.isArray(modes)) {
    return { modes: [] }
  }
  return {
    modes: Object.entries(
      modes as Record<string, { label?: string; input_field?: string } | null>
    ).map(([key, mode]) => ({
      key,
      label: mode?.label ?? key,
      input_field: mode?.input_field ?? '',
    })),
  }
}
