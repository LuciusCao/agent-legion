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
  /** 检查器节点身份（codex 四轮 P1 on #427）：本地输入的跨节点重置依据。
   * A、B 都未绑定（或绑定同 key）时 props.name 不变，仅靠 name 同步会让
   * A 的未校验待选目录残留到 B——在 B 上点「校验」会把 A 的目录绑到 B。 */
  nodeKey: string
}

/** Skill 目录名输入行（自 SkillSelector 拆出，文件预算）。候选目录经
 * datalist 自动补全（#327）：输入值与候选精确一致（下拉选中或完整手打）
 * 即触发校验回填；其他手打内容仍走「校验」按钮，行为与拆出前一致。
 * 外部绑定变化时输入跟随（codex 二轮 P1 on #427）：校验请求恒经 workspace
 * 前缀发出，回填 key 派生的目录名与发起校验的输入一致，同步不会打断用户
 * 输入；props.value 不变时（普通编辑）本地输入保持不动；节点身份（nodeKey）
 * 变化时无条件重置（codex 四轮 P1 on #427）——未绑定节点间的切换 props.name
 * 不变，仅按 name 同步无法清掉上一节点的待选目录。 */
export function SkillDirectoryInput(props: Props) {
  const [name, setName] = useState(props.name)
  const directories = useSkillDirectories(props.workspaceId)

  useEffect(() => {
    // 外部绑定变化（节点切换/换绑/清除）时接管输入（codex 二轮 P1 on #427，
    // 与 useStudioChatQueue 会话切换重置同一模式）；props.name 不变时本地
    // 输入保持不动，用户编辑不被同名回显打断。nodeKey 变化（检查器切换
    // 节点）时即便 props.name 相同也重置（codex 四轮 P1 on #427）：setName
    // 的函数式更新对同值是 no-op，绑定值不变的节点切换不会误清空。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部受控值变化时同步本地回显
    setName(props.name)
  }, [props.name, props.nodeKey])

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
