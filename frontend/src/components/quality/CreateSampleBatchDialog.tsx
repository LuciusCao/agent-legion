import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from '@mui/material'
import { toErrorMessage } from '../../lib/queryError'
import { useCreateSampleBatch } from '../../hooks/useQuality'
import type { QualitySampleBatchCreateResponse } from '../../api/qualityApi'

export interface CreateSampleBatchDialogProps {
  open: boolean
  workspaceId: string
  onClose: () => void
  onCreated: (batch: QualitySampleBatchCreateResponse) => void
}

function parseNodeKeys(raw: string): string[] | undefined {
  const keys = raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  return keys.length ? keys : undefined
}

function toIso(raw: string): string | undefined {
  if (!raw) return undefined
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

/** 新建抽样批次对话框。seed 留空时由服务端生成。 */
export function CreateSampleBatchDialog({
  open,
  workspaceId,
  onClose,
  onCreated,
}: CreateSampleBatchDialogProps) {
  const [name, setName] = useState('')
  const [sampleSize, setSampleSize] = useState('20')
  const [nodeKeys, setNodeKeys] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [seed, setSeed] = useState('')
  const [error, setError] = useState('')
  const mutation = useCreateSampleBatch(workspaceId)

  if (!open) return null

  const size = Number(sampleSize)
  const sizeValid = Number.isInteger(size) && size >= 1 && size <= 1000
  const canSubmit = name.trim().length > 0 && sizeValid && !mutation.isPending

  const reset = () => {
    setName('')
    setSampleSize('20')
    setNodeKeys('')
    setSince('')
    setUntil('')
    setSeed('')
    setError('')
  }

  const handleSubmit = async () => {
    setError('')
    try {
      const batch = await mutation.mutateAsync({
        name: name.trim(),
        sample_size: size,
        seed: seed.trim() || null,
        filters: {
          node_keys: parseNodeKeys(nodeKeys) ?? null,
          since: toIso(since) ?? null,
          until: toIso(until) ?? null,
        },
      })
      reset()
      onCreated(batch)
    } catch (err) {
      setError(toErrorMessage(err))
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>新建抽样</DialogTitle>
      <DialogContent
        sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}
      >
        <TextField
          label="名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          size="small"
          autoFocus
        />
        <TextField
          label="样本量"
          type="number"
          value={sampleSize}
          onChange={(e) => setSampleSize(e.target.value)}
          error={!sizeValid}
          helperText={sizeValid ? '1–1000' : '样本量需为 1–1000 的整数'}
          size="small"
          inputProps={{ min: 1, max: 1000 }}
        />
        <TextField
          label="节点（逗号分隔，可选）"
          value={nodeKeys}
          onChange={(e) => setNodeKeys(e.target.value)}
          placeholder="generate_key_info, review_key_info"
          size="small"
        />
        <TextField
          label="起始时间（可选）"
          type="datetime-local"
          value={since}
          onChange={(e) => setSince(e.target.value)}
          size="small"
          InputLabelProps={{ shrink: true }}
        />
        <TextField
          label="结束时间（可选）"
          type="datetime-local"
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          size="small"
          InputLabelProps={{ shrink: true }}
        />
        <TextField
          label="Seed（可选，留空由服务端生成）"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
          size="small"
        />
        {error && <p role="alert">{error}</p>}
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={mutation.isPending}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!canSubmit}
        >
          {mutation.isPending ? '创建中…' : '创建'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
