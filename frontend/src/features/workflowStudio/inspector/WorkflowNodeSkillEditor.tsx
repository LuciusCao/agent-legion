import { Button, TextField } from '@mui/material'
import type { WorkflowNodeRecord } from '../../../types'
import { useSettingStore } from '../../../stores/settingStore'
import { SkillSelector } from '../../../components/SkillSelector'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import {
  normalizeNodeSkill,
  patchWorkflowNodeSkill,
} from '../shared/workflowStudioYamlDraft.skill'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

/** #76：节点级 skill 内容绑定编辑（仅 Agent 路由节点渲染，由调用方判定）。
 * 编辑真相源是草稿 YAML——key 经 SkillSelector 校验填入，ref 为普通文本
 * （留空回落 skill_sources 默认 ref）；response 的 node.skill 只作已发布
 * 状态的回显兜底。回写经 patchWorkflowNodeSkill，草稿持久化走既有的
 * debounce PUT raw yaml 流（字段无关）。 */
export function WorkflowNodeSkillEditor(props: Props) {
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 区分「草稿里没有这个节点」（回显 published 绑定）与「草稿节点存在但无
  // skill key」（用户显式清除，不回显——否则清除立刻被 published 覆盖，
  // codex P2 on PR 317）。
  const draftNode = parseWorkflowNode(props.definitionYaml, props.node.key)
  const draftSkill = normalizeNodeSkill(draftNode?.skill)
  const published = props.node.skill
    ? { key: props.node.skill.key, ref: props.node.skill.ref }
    : null
  const bound = draftNode === undefined ? published : draftSkill

  function patch(skill: { key: string; ref: string } | null) {
    props.setDefinitionYaml(
      patchWorkflowNodeSkill(props.definitionYaml, props.node.key, skill)
    )
  }

  if (props.readOnly) {
    return bound ? (
      <TextField
        label="Skill"
        variant="outlined"
        value={`${bound.key}${bound.ref ? ` @ ${bound.ref}` : ''}`}
        fullWidth
        slotProps={{ input: { readOnly: true } }}
      />
    ) : null
  }

  return (
    <div>
      {workspaceId && (
        <SkillSelector
          workspaceId={workspaceId}
          value={bound?.key ?? ''}
          onChange={(key) => patch({ key, ref: bound?.ref ?? '' })}
        />
      )}
      <TextField
        label="Skill ref"
        variant="outlined"
        value={bound?.ref ?? ''}
        onChange={(event) => {
          if (!bound) return
          patch({ key: bound.key, ref: event.target.value })
        }}
        fullWidth
        disabled={!bound}
        placeholder="留空用源默认 ref"
        helperText={bound ? undefined : '先经上方校验选择 skill'}
        sx={{ mt: 1.5 }}
      />
      {bound && (
        <Button size="small" sx={{ mt: 0.5 }} onClick={() => patch(null)}>
          清除 skill 绑定
        </Button>
      )}
    </div>
  )
}
