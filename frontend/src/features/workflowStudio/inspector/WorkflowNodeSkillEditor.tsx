import { Button, TextField } from '@mui/material'
import type { WorkflowNodeRecord } from '../../../types'
import { useSettingStore } from '../../../stores/settingStore'
import { SkillSelector } from '../../../components/SkillSelector'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import {
  normalizeNodeSkill,
  patchWorkflowNodeSkill,
} from '../shared/workflowStudioYamlDraft.skill'
import { useLatestSkillRunVersion } from './useLatestSkillRunVersion'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

/** #76：节点级 skill 内容绑定编辑（仅 Agent 路由节点渲染，由调用方判定）。
 * #410 起选择链路合一为两控件：SkillSelector 一个组件承载「目录名（校验
 * 后绑定 key）+ 版本下拉（latest 跟随 HEAD / 具体 tag 首次 dispatch 冻结进
 * skill_lock）」，不再有只读 Skill 回显字段、参考 tag 下拉或独立 Skill ref
 * 输入。编辑真相源是草稿 YAML，回写经 patchWorkflowNodeSkill（恒 mapping
 * 形态，对齐后端 echo），response 的 node.skill 只作已发布状态的回显兜底。
 * 换绑 skill 时 ref 重置为 latest（codex 二轮 P1 on #427）。 */
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
  const latestEcho = useLatestSkillRunVersion(
    workspaceId,
    props.node.key,
    bound?.ref === 'latest' || bound?.ref === ''
  )

  function patch(skill: { key: string; ref: string } | null) {
    props.setDefinitionYaml(
      patchWorkflowNodeSkill(props.definitionYaml, props.node.key, skill)
    )
  }

  // 校验回填 skill key（codex 二轮 P1 on #427）：换绑不同 skill 时不携带
  // 旧 skill 的 tag——B@旧tag 无法被 B 的仓库解析，发布后首次 dispatch 才
  // 失败；跟 HEAD 的默认策略是 latest。同一 key 重新校验则保留已选版本。
  function handleSkillKeyChange(key: string) {
    patch({
      key,
      ref: key === bound?.key ? (bound?.ref ?? 'latest') : 'latest',
    })
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
          onChange={handleSkillKeyChange}
          skillRef={bound?.ref ?? ''}
          onSkillRefChange={(ref) => {
            if (!bound) return
            patch({ key: bound.key, ref })
          }}
        />
      )}
      {latestEcho && (
        <p style={{ fontSize: 12, color: '#616161', marginTop: 8 }}>
          实际执行：{latestEcho}
        </p>
      )}
      {bound && (
        <Button size="small" sx={{ mt: 0.5 }} onClick={() => patch(null)}>
          清除 skill 绑定
        </Button>
      )}
    </div>
  )
}
