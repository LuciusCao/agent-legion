import { useEffect, useState } from 'react'
import { Button, InputAdornment, TextField } from '@mui/material'
import { SkillDirectoryDatalist } from './SkillDirectoryDatalist'
import { useSkillDirectories } from './useSkillDirectories'

const DIRECTORY_LIST_ID = 'skill-directory-options'

type Props = {
  /** workspace 技能根前缀（只读 adornment，形如 ~/.agents/skills/<ws>/）。 */
  prefix: string
  workspaceId: string
  /** 技能根加载完成前禁用输入。 */
  rootReady: boolean
  validating: boolean
  onValidate: (name: string) => void
  /** 每次输入变化都会回调（早于精确匹配判定），宿主借此作废在飞的校验。 */
  onEdit: () => void
  /** 当前绑定回显的目录名（codex 二轮 P1 on #427）：外部 key 变化（含检查
   * 器切换节点）时输入跟随；'' = 未绑定。 */
  name: string
}

/** Skill 目录名输入行（自 SkillSelector 拆出，文件预算）。候选目录经
 * datalist 自动补全（#327）：输入值与候选精确一致（下拉选中或完整手打）
 * 即触发校验回填；其他手打内容仍走「校验」按钮，行为与拆出前一致。
 * 外部绑定变化时输入跟随（codex 二轮 P1 on #427）：校验请求恒经 workspace
 * 前缀发出，回填 key 派生的目录名与发起校验的输入一致，同步不会打断用户
 * 输入；props.value 不变时（普通编辑）本地输入保持不动。 */
export function SkillDirectoryInput(props: Props) {
  const [name, setName] = useState(props.name)
  const directories = useSkillDirectories(props.workspaceId)

  useEffect(() => {
    // 外部绑定变化（节点切换/换绑/清除）时接管输入（codex 二轮 P1 on #427，
    // 与 useStudioChatQueue 会话切换重置同一模式）；props.name 不变时本地
    // 输入保持不动，用户编辑不被同名回显打断。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部受控值变化时同步本地回显
    setName((current) => (current === props.name ? current : props.name))
  }, [props.name])

  function handleChange(next: string) {
    setName(next)
    // 任何编辑都先作废旧校验：否则继续输入到非候选值时在飞响应仍被视为
    // 最新，会把上一个候选的 skill key 回填到当前输入之上（codex P1 on #341）。
    props.onEdit()
    if (directories.includes(next.trim())) props.onValidate(next)
  }

  return (
    <div
      style={{ display: 'flex', gap: 12, marginTop: 12, alignItems: 'start' }}
    >
      <TextField
        label="Skill 目录名"
        variant="outlined"
        value={name}
        onChange={(e) => handleChange(e.target.value)}
        fullWidth
        placeholder="write-script"
        disabled={!props.rootReady}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">{props.prefix}</InputAdornment>
            ),
          },
          htmlInput: { list: DIRECTORY_LIST_ID },
        }}
      />
      <Button
        variant="outlined"
        onClick={() => props.onValidate(name)}
        disabled={!props.rootReady || props.validating || name.trim() === ''}
        sx={{ flexShrink: 0, mt: 1 }}
      >
        {props.validating ? '校验中...' : '校验'}
      </Button>
      <SkillDirectoryDatalist id={DIRECTORY_LIST_ID} options={directories} />
    </div>
  )
}
