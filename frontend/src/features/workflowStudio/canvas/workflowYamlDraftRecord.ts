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
  // 形状校验：语法合法但形状残缺的草稿（如 `nodes:\n  review:` 值为 null、
  // nodes 是数组、edges 不是数组/边为 null）不得在渲染期抛异常，一律返回
  // null 走「回退 published + 警示 chip」路径。
  if (!isPlainObject(parsed.nodes ?? {})) return null
  if (!Array.isArray(parsed.edges ?? [])) return null
  const rawNodes = Object.entries(parsed.nodes ?? {})
  if (rawNodes.some(([, node]) => !isPlainObject(node))) return null
  const rawEdges = parsed.edges ?? []
  if (rawEdges.some((edge) => !isPlainObject(edge))) return null
  return {
    key: parsed.key ?? '',
    label: parsed.label ?? parsed.key ?? '',
    intake: mapIntake(parsed.intake),
    nodes: rawNodes.map(([key, node]) => mapNode(key, node)),
    edges: rawEdges
      .filter((edge) => edge.from && edge.to)
      .map((edge) => ({
        source: edge.from as string,
        target: edge.to as string,
        condition: edge.when?.path
          ? {
              artifact: edge.when.artifact ?? '',
              path: edge.when.path,
              equals: edge.when.equals,
            }
          : null,
      })),
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
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
