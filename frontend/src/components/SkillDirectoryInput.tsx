import { useState } from 'react'
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
}

/** Skill 目录名输入行（自 SkillSelector 拆出，文件预算）。候选目录经
 * datalist 自动补全（#327）：输入值与候选精确一致（下拉选中或完整手打）
 * 即触发校验回填；其他手打内容仍走「校验」按钮，行为与拆出前一致。 */
export function SkillDirectoryInput(props: Props) {
  const [name, setName] = useState('')
  const directories = useSkillDirectories(props.workspaceId)

  function handleChange(next: string) {
    setName(next)
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
