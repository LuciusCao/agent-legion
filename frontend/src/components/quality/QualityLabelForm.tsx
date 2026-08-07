import { useState } from 'react'
import {
  Button,
  Chip,
  FormControlLabel,
  Radio,
  RadioGroup,
  TextField,
} from '@mui/material'
import { toErrorMessage } from '../../lib/queryError'
import { useAddSampleItemLabel } from '../../hooks/useQuality'
import { QUALITY_REASON_CODES } from '../../api/qualityApi'
import styles from './QualityPanel.module.css'

export interface QualityLabelFormProps {
  workspaceId: string
  itemId: string
  /** 提供时打标目标为 replay（target='replay'），否则为原 run。 */
  replayId?: string
  /** aria/按钮文案前缀，用于同页多个表单时区分（如 replay 表单）。 */
  verdictLabel?: string
  reasonLabel?: string
  submitText?: string
}

/** good/bad + reason codes + note 打标表单；bad 需至少一个原因码。 */
export function QualityLabelForm({
  workspaceId,
  itemId,
  replayId,
  verdictLabel = '结论',
  reasonLabel = '原因码',
  submitText = '提交打标',
}: QualityLabelFormProps) {
  const mutation = useAddSampleItemLabel(workspaceId, itemId)
  const [verdict, setVerdict] = useState<'good' | 'bad'>('good')
  const [reasons, setReasons] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [submitError, setSubmitError] = useState('')

  const toggleReason = (code: string) => {
    setReasons((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    )
  }

  const canSubmit =
    !mutation.isPending && (verdict === 'good' || reasons.length > 0)

  const handleSubmit = async () => {
    setSubmitError('')
    try {
      await mutation.mutateAsync({
        verdict,
        reason_codes: reasons,
        note,
        ...(replayId ? { replay_id: replayId } : {}),
      })
      setReasons([])
      setNote('')
    } catch (err) {
      setSubmitError(toErrorMessage(err))
    }
  }

  return (
    <div>
      <RadioGroup
        row
        aria-label={verdictLabel}
        value={verdict}
        onChange={(e) => setVerdict(e.target.value as 'good' | 'bad')}
      >
        <FormControlLabel value="good" control={<Radio />} label="good" />
        <FormControlLabel value="bad" control={<Radio />} label="bad" />
      </RadioGroup>
      <div className={styles.reasonChips} role="group" aria-label={reasonLabel}>
        {QUALITY_REASON_CODES.map((code) => (
          <Chip
            key={code}
            label={code}
            size="small"
            variant={reasons.includes(code) ? 'filled' : 'outlined'}
            color={reasons.includes(code) ? 'primary' : 'default'}
            onClick={() => toggleReason(code)}
          />
        ))}
      </div>
      {verdict === 'bad' && reasons.length === 0 && (
        <p className={styles.muted}>bad 结论需至少选择一个原因码</p>
      )}
      <TextField
        label="备注（可选）"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        multiline
        minRows={2}
        size="small"
        fullWidth
      />
      {submitError && <p role="alert">{submitError}</p>}
      <div className={styles.toolbar}>
        <Button
          variant="contained"
          size="small"
          onClick={handleSubmit}
          disabled={!canSubmit}
        >
          {mutation.isPending ? '提交中…' : submitText}
        </Button>
      </div>
    </div>
  )
}
