import { useState } from 'react'
import { Button, InputAdornment, MenuItem, TextField } from '@mui/material'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'

type Props = {
  /** 当前 workspace（workspace 技能默认目录 ~/.agents/skills/<workspaceId>/）。 */
  workspaceId: string
  /** Currently selected skill key (filled by a successful validation). */
  value: string
  onChange: (skillKey: string) => void
}

/**
 * Skill picker for the Agent editor: validates a skill directory under the
 * workspace-scoped skills root (~/.agents/skills/<workspaceId>/) via
 * POST /api/skills/validate, fills the skill key on success, and shows the
 * repo tags as reference info. Tag selection never writes back — the DB skill
 * lock (global_settings skill_lock) stays the single source of truth for the
 * locked ref. The validator expands `~` server-side, so the composed path is
 * sent with the tilde prefix as-is.
 */
export function SkillSelector({ workspaceId, value, onChange }: Props) {
  const prefix = `~/.agents/skills/${workspaceId}/`
  const [name, setName] = useState('')
  const [validating, setValidating] = useState(false)
  const [result, setResult] = useState<SkillValidateResponse | null>(null)
  const [selectedTag, setSelectedTag] = useState('')
  const [tagTouched, setTagTouched] = useState(false)

  async function handleValidate() {
    const relative = name.trim().replace(/^\/+/, '')
    if (!relative) return
    const fullPath = `${prefix}${relative}`
    setValidating(true)
    try {
      const next = await validateSkillPath(fullPath)
      setResult(next)
      setSelectedTag(next.latest_tag ?? '')
      setTagTouched(false)
      if (next.valid && next.skill_key) onChange(next.skill_key)
    } catch (err) {
      setResult({
        valid: false,
        path: fullPath,
        error: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setValidating(false)
    }
  }

  const tags = result?.valid ? (result.tags ?? []) : []

  return (
    <div>
      <TextField
        label="Skill"
        variant="outlined"
        value={value}
        fullWidth
        slotProps={{ input: { readOnly: true } }}
        helperText="通过下方目录名校验自动填入"
      />
      <div
        style={{ display: 'flex', gap: 12, marginTop: 12, alignItems: 'start' }}
      >
        <TextField
          label="Skill 目录名"
          variant="outlined"
          value={name}
          onChange={(e) => setName(e.target.value)}
          fullWidth
          placeholder="write-script"
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">{prefix}</InputAdornment>
              ),
            },
          }}
        />
        <Button
          variant="outlined"
          onClick={() => void handleValidate()}
          disabled={validating || name.trim() === ''}
          sx={{ flexShrink: 0, mt: 1 }}
        >
          {validating ? '校验中...' : '校验'}
        </Button>
      </div>
      {result && !result.valid && (
        <p role="alert" style={{ color: '#d32f2f', fontSize: 13 }}>
          {result.error || 'Skill 路径校验失败'}
        </p>
      )}
      {result?.valid && (
        <div style={{ marginTop: 12 }}>
          {tags.length > 0 && (
            <TextField
              select
              label="可用 tag（参考）"
              variant="outlined"
              value={selectedTag}
              onChange={(e) => {
                setSelectedTag(e.target.value)
                setTagTouched(true)
              }}
              fullWidth
            >
              {tags.map((tag) => (
                <MenuItem key={tag} value={tag}>
                  {tag}
                  {tag === result.latest_tag ? '（最新）' : ''}
                </MenuItem>
              ))}
            </TextField>
          )}
          {result.locked_ref && (
            <p style={{ fontSize: 12, color: '#616161' }}>
              当前锁定 ref：{result.locked_ref}
            </p>
          )}
          {tagTouched && (
            <p style={{ fontSize: 12, color: '#ed6c02' }}>
              tag 变更需通过 skills 同步流程生效，此处选择不会修改锁定 ref。
            </p>
          )}
        </div>
      )}
    </div>
  )
}
