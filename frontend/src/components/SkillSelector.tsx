import { useRef, useState } from 'react'
import { TextField } from '@mui/material'
import { validateSkillPath } from '../api'
import type { SkillValidateResponse } from '../types'
import { SkillDirectoryInput } from './SkillDirectoryInput'
import { SkillValidationResult } from './SkillValidationResult'
import {
  FALLBACK_SKILLS_ROOT,
  useSkillsRootPrefix,
} from './useSkillsRootPrefix'

type Props = {
  /** 当前 workspace（workspace 技能默认目录 ~/.agents/skills/<workspaceId>/）。 */
  workspaceId: string
  /** Currently selected skill key (filled by a successful validation). */
  value: string
  onChange: (skillKey: string) => void
}

/**
 * Skill picker for the Agent editor: the directory-name row
 * (SkillDirectoryInput) offers the workspace's existing skill directories as
 * datalist candidates (#327) — picking one (or typing its exact name) runs the
 * validation immediately, anything else is validated via the 校验 button.
 * Validation posts the composed path to POST /api/skills/validate, fills the
 * skill key on success, and shows the repo tags as reference info. The skills
 * root comes from the read-only `skills_root` field of GET
 * /api/admin/instance-settings (single source: backend skill_roots.py); while
 * it loads the input stays disabled, and on load failure it falls back to the
 * default root with a hint. Tag selection never writes back — the DB skill
 * lock (global_settings skill_lock) stays the single source of truth for the
 * locked ref. The validator expands `~` server-side, so the composed path is
 * sent with the tilde prefix as-is. Consecutive picks (e.g. prefix-related candidate names
 * like review / review-questions) can overlap in flight: a monotonic sequence number makes
 * every response after a newer request a no-op, so a late earlier response can never
 * overwrite the latest pick.
 */
export function SkillSelector({ workspaceId, value, onChange }: Props) {
  const { prefix, rootReady, rootLoadFailed } = useSkillsRootPrefix(workspaceId)
  const [validating, setValidating] = useState(false)
  const [result, setResult] = useState<SkillValidateResponse | null>(null)
  const [selectedTag, setSelectedTag] = useState('')
  const [tagTouched, setTagTouched] = useState(false)
  const validateSeq = useRef(0)

  async function handleValidate(rawName: string) {
    const relative = rawName.trim().replace(/^\/+/, '')
    if (!relative) return
    const seq = ++validateSeq.current
    const fullPath = `${prefix}${relative}`
    setValidating(true)
    try {
      const next = await validateSkillPath(fullPath)
      // 已有更新的校验在飞：丢弃过期响应，不覆盖最终结果（codex P1 on #336）。
      if (seq !== validateSeq.current) return
      setResult(next)
      setSelectedTag(next.latest_tag ?? '')
      setTagTouched(false)
      if (next.valid && next.skill_key) onChange(next.skill_key)
    } catch (err) {
      if (seq !== validateSeq.current) return
      setResult({
        valid: false,
        path: fullPath,
        error: err instanceof Error ? err.message : String(err),
      })
    } finally {
      if (seq === validateSeq.current) setValidating(false)
    }
  }

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
      <SkillDirectoryInput
        prefix={prefix}
        workspaceId={workspaceId}
        rootReady={rootReady}
        validating={validating}
        onValidate={(name) => void handleValidate(name)}
        onEdit={() => {
          // 输入一旦变化，在飞的校验结果即过期（codex P1 on #341）。
          validateSeq.current += 1
        }}
      />
      {rootLoadFailed && (
        <p style={{ color: '#ed6c02', fontSize: 12 }}>
          实例设置加载失败，技能根目录回退为默认 {FALLBACK_SKILLS_ROOT}。
        </p>
      )}
      {result && !result.valid && (
        <p role="alert" style={{ color: '#d32f2f', fontSize: 13 }}>
          {result.error || 'Skill 路径校验失败'}
        </p>
      )}
      {result?.valid && (
        <SkillValidationResult
          result={result}
          selectedTag={selectedTag}
          tagTouched={tagTouched}
          onSelectTag={(tag) => {
            setSelectedTag(tag)
            setTagTouched(true)
          }}
        />
      )}
    </div>
  )
}
