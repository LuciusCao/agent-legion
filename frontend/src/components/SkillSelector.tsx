import { useState } from 'react'
import { Button, MenuItem, TextField } from '@mui/material'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'

type Props = {
  /** Currently selected skill key (filled by a successful validation). */
  value: string
  onChange: (skillKey: string) => void
}

/**
 * Skill picker for the Agent editor: validates an absolute skill path via
 * POST /api/skills/validate, fills the skill key on success, and shows the
 * repo tags as reference info. Tag selection never writes back — the DB skill
 * lock (global_settings skill_lock) stays the single source of truth for the
 * locked ref.
 */
export function SkillSelector({ value, onChange }: Props) {
  const [path, setPath] = useState('')
  const [validating, setValidating] = useState(false)
  const [result, setResult] = useState<SkillValidateResponse | null>(null)
  const [selectedTag, setSelectedTag] = useState('')
  const [tagTouched, setTagTouched] = useState(false)

  async function handleValidate() {
    const trimmed = path.trim()
    if (!trimmed) return
    setValidating(true)
    try {
      const next = await validateSkillPath(trimmed)
      setResult(next)
      setSelectedTag(next.latest_tag ?? '')
      setTagTouched(false)
      if (next.valid && next.skill_key) onChange(next.skill_key)
    } catch (err) {
      setResult({
        valid: false,
        path: trimmed,
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
        helperText="通过下方路径校验自动填入"
      />
      <div
        style={{ display: 'flex', gap: 12, marginTop: 12, alignItems: 'start' }}
      >
        <TextField
          label="Skill 路径（绝对路径）"
          variant="outlined"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          fullWidth
          placeholder="/path/to/skills/some_skill"
        />
        <Button
          variant="outlined"
          onClick={() => void handleValidate()}
          disabled={validating || path.trim() === ''}
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
