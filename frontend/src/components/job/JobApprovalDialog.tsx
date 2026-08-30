import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  TextField,
} from '@mui/material'
import type { JobNode } from '../../types/jobTypes'
import type {
  ApprovalDecision,
  ApprovalVerdict,
} from '../../api/jobApprovalApi'
import { fetchApprovalDecisions } from '../../api/jobApprovalApi'
import { MaterialIcon } from '../MaterialIcon'
import styles from './JobApprovalDialog.module.css'

export interface JobApprovalDialogProps {
  open: boolean
  workspaceId: string
  jobId: string
  gate: JobNode
  nodes: JobNode[]
  loading?: boolean
  onPreviewArtifact: (name: string) => void
  onDecide: (
    verdict: ApprovalVerdict,
    note: string,
    reworkTarget: string
  ) => Promise<void>
  onClose: () => void
}

const VERDICT_LABELS: Record<ApprovalVerdict, string> = {
  approved: '通过',
  rework: '打回重做',
  rejected: '终止',
}

/** All ancestors of the gate inside this job's node set (start never enters job_nodes). */
function ancestorKeys(gate: JobNode, nodes: JobNode[]): string[] {
  const byKey = new Map(nodes.map((node) => [node.node_key, node]))
  const seen = new Set<string>()
  const stack = [...(gate.after ?? [])]
  while (stack.length > 0) {
    const key = stack.pop()!
    if (seen.has(key) || !byKey.has(key)) continue
    seen.add(key)
    stack.push(...(byKey.get(key)!.after ?? []))
  }
  return nodes.map((n) => n.node_key).filter((key) => seen.has(key))
}

export function JobApprovalDialog(props: JobApprovalDialogProps) {
  // Unmount while closed so a reopen remounts with fresh form state — no
  // synchronous reset-in-effect needed.
  if (!props.open) return null
  return <JobApprovalDialogContent {...props} />
}

function JobApprovalDialogContent({
  open,
  workspaceId,
  jobId,
  gate,
  nodes,
  loading = false,
  onPreviewArtifact,
  onDecide,
  onClose,
}: JobApprovalDialogProps) {
  const [verdict, setVerdict] = useState<ApprovalVerdict>('approved')
  const [note, setNote] = useState('')
  const [reworkTarget, setReworkTarget] = useState('')
  const [history, setHistory] = useState<ApprovalDecision[]>([])

  useEffect(() => {
    let cancelled = false
    fetchApprovalDecisions(workspaceId, jobId)
      .then((decisions) => {
        if (!cancelled) {
          setHistory(decisions.filter((d) => d.node_key === gate.node_key))
        }
      })
      .catch(() => {
        if (!cancelled) setHistory([])
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, jobId, gate.node_key])

  const ancestors = useMemo(() => ancestorKeys(gate, nodes), [gate, nodes])
  const labelByKey = useMemo(
    () => new Map(nodes.map((node) => [node.node_key, node.label])),
    [nodes]
  )

  const noteMissing = verdict === 'rework' && note.trim() === ''
  const submitDisabled = loading || noteMissing

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '640px', width: '92vw' } }}
    >
      <DialogTitle>审批 · {gate.label}</DialogTitle>
      <DialogContent className={styles.content}>
        {gate.inputs.length > 0 && (
          <section className={styles.section}>
            <div className={styles.sectionTitle}>审阅材料</div>
            <div className={styles.materials}>
              {gate.inputs.map((name) => (
                <Button
                  key={name}
                  size="small"
                  variant="outlined"
                  startIcon={<MaterialIcon name="description" />}
                  onClick={() => onPreviewArtifact(name)}
                >
                  {name}
                </Button>
              ))}
            </div>
          </section>
        )}

        {history.length > 0 && (
          <section className={styles.section}>
            <div className={styles.sectionTitle}>审批历史</div>
            <ul className={styles.history}>
              {history.map((decision) => (
                <li key={decision.id}>
                  <span className={styles.historyVerdict}>
                    {VERDICT_LABELS[decision.verdict]}
                  </span>
                  {decision.note && (
                    <span className={styles.historyNote}>{decision.note}</span>
                  )}
                  <span className={styles.historyMeta}>
                    {decision.decided_by.replace(/^user:/, '')}
                    {decision.created_at
                      ? ` · ${new Date(decision.created_at).toLocaleString()}`
                      : ''}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className={styles.section}>
          <div className={styles.sectionTitle}>决定</div>
          <RadioGroup
            row
            value={verdict}
            onChange={(e) => setVerdict(e.target.value as ApprovalVerdict)}
          >
            <FormControlLabel
              value="approved"
              control={<Radio />}
              label="通过"
            />
            <FormControlLabel
              value="rework"
              control={<Radio />}
              label="打回重做"
            />
            <FormControlLabel
              value="rejected"
              control={<Radio />}
              label="终止"
            />
          </RadioGroup>
        </section>

        {verdict === 'rework' && (
          <FormControl size="small" fullWidth>
            <InputLabel id="rework-target-label">重做起点</InputLabel>
            <Select
              labelId="rework-target-label"
              label="重做起点"
              value={reworkTarget}
              onChange={(e) => setReworkTarget(e.target.value)}
            >
              <MenuItem value="">节点默认配置</MenuItem>
              {ancestors.map((key) => (
                <MenuItem key={key} value={key}>
                  {labelByKey.get(key) ?? key}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <TextField
          label={
            verdict === 'rework'
              ? '修改意见（必填，将作为重写输入）'
              : '备注（可选）'
          }
          value={note}
          onChange={(e) => setNote(e.target.value)}
          multiline
          minRows={3}
          fullWidth
          required={verdict === 'rework'}
          error={noteMissing && note !== ''}
          helperText={
            verdict === 'rework'
              ? '意见会写入产物文件，上游 Agent 重写时会读取它。'
              : verdict === 'rejected'
                ? '终止后该任务标记失败，批次中其他任务不受影响。'
                : undefined
          }
        />
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={loading}>
          取消
        </Button>
        <Button
          variant="contained"
          color={
            verdict === 'approved'
              ? 'success'
              : verdict === 'rework'
                ? 'warning'
                : 'error'
          }
          disabled={submitDisabled}
          onClick={() => void onDecide(verdict, note, reworkTarget)}
        >
          {VERDICT_LABELS[verdict]}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
