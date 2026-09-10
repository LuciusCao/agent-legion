import { useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  Step,
  StepLabel,
  Stepper,
  TextField,
} from '@mui/material'
import { useUiStore } from '../../stores/uiStore'
import {
  createCampaign,
  createCampaignFromManifest,
  createSubmitCampaign,
  previewCampaign,
} from '../../api/campaignApi'
import type {
  CampaignMode,
  CampaignPreviewRequest,
  CampaignPreviewResponse,
  CampaignRerunTarget,
  CampaignSubmitInlineTarget,
} from '../../types/campaignTypes'

type BatchCreateWizardProps = {
  open: boolean
  workspaceId: string
  onClose: () => void
  onCreated: () => void
}

type WizardMode = CampaignMode
type SubmitChannel = 'inline' | 'upload'

const STEPS = ['做什么', '选目标', '确认'] as const

const MODE_HINTS: Record<WizardMode, string> = {
  submit:
    '按清单新建任务：粘贴 ID 或上传清单文件。任务会按执行节奏自动分批创建。',
  rerun: '把符合条件的任务翻回重跑：可从失败节点或指定节点开始。',
  upgrade: '把旧版本工作流的任务升级到当前版本并从头重跑。',
}

/**
 * 「新建批量任务」三步向导（#532 PR-D 定稿）：做什么 → 选目标 → 确认。
 *
 * 用户对 campaign / submit / watermark / manifest 等实现词无感——目标步骤
 * 只暴露任务类型语义的输入（清单粘贴 / 清单文件 / 筛选 JSON + 节点 /
 * 升级筛选），确认步骤统一文案「按执行节奏自动分批」。水印/批大小留给
 * 高级输入（留空走实例默认）。
 */
export function BatchCreateWizard({
  open,
  workspaceId,
  onClose,
  onCreated,
}: BatchCreateWizardProps) {
  const { showToast } = useUiStore()
  const [step, setStep] = useState(0)
  const [mode, setMode] = useState<WizardMode>('submit')
  const [nameText, setNameText] = useState('')
  const [jobIdsText, setJobIdsText] = useState('')
  const [filterText, setFilterText] = useState('')
  const [useFilter, setUseFilter] = useState(true)
  const [nodeKey, setNodeKey] = useState('')
  const [fromFailedNode, setFromFailedNode] = useState(true)
  const [watermarkText, setWatermarkText] = useState('')
  const [batchSizeText, setBatchSizeText] = useState('')
  const [submitChannel, setSubmitChannel] = useState<SubmitChannel>('inline')
  const [itemsText, setItemsText] = useState('')
  const [connectionKey, setConnectionKey] = useState('')
  const [manifestFile, setManifestFile] = useState<File | null>(null)
  // 试算结果绑定请求快照（审核 P2）：任一塑形输入变化后旧结果自然失效
  // （快照失配即不展示），确认页不会残留按旧条件算出的数量；在途响应
  // 由请求令牌丢弃，不许过期结果写入状态。
  const [preview, setPreview] = useState<{
    key: string
    result: CampaignPreviewResponse['result'] | null
    error: string | null
  } | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const parsedKnobs = useMemo(() => {
    const watermark = watermarkText.trim() === '' ? null : Number(watermarkText)
    const batch_size =
      batchSizeText.trim() === '' ? null : Number(batchSizeText)
    return {
      watermark:
        watermark != null && Number.isFinite(watermark) && watermark >= 1
          ? Math.floor(watermark)
          : null,
      batch_size:
        batch_size != null && Number.isFinite(batch_size) && batch_size >= 1
          ? Math.floor(batch_size)
          : null,
    }
  }, [watermarkText, batchSizeText])

  // 「粘贴 ID」通道：每行一个外部 ID（connection_key 共享），转 ref items。
  const parsedInline = useMemo(() => {
    if (submitChannel !== 'inline') return null
    const lines = itemsText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (lines.length === 0) {
      return {
        items: [] as CampaignSubmitInlineTarget['items'],
        error: null,
        usesSharedKey: false,
      }
    }
    // 整行 JSON 逐行分拣：对象行按 JSON 解析（自带连接，豁免 Key）；纯
    // ID 行共享 Key（必填）。可混排，对象行不得降级成裸 ID（二轮 P2）。
    const refItems = (ids: string[]) =>
      ids.map((externalId) => ({
        type: 'ref' as const,
        connection_key: connectionKey.trim(),
        external_id: externalId,
      }))

    const objLines = lines.filter((line) => line.startsWith('{'))
    if (objLines.length === 0)
      return { items: refItems(lines), error: null, usesSharedKey: true }
    // 任一 ID 行存在即按共享 Key 必填（与纯 ID 模式同一门槛）。
    const idItems = refItems(lines.filter((line) => !line.startsWith('{')))
    const parsed = parseJsonlItems(objLines.join('\n'), idItems)
    return { ...parsed, usesSharedKey: idItems.length > 0 }
  }, [submitChannel, itemsText, connectionKey])
  const inlineCount = parsedInline?.items.length ?? 0

  // 审核 P2：粘贴的纯 ID 行按连接 Key 关联外部数据源，Key 为空时后端
  // 必拒（连接标识最短 1 字符）——必须卡在「下一步」之前并给出提示，
  // 不能等提交才报错。对象行（自带连接信息）不适用。
  const connectionKeyMissing =
    mode === 'submit' &&
    submitChannel === 'inline' &&
    parsedInline?.usesSharedKey === true &&
    connectionKey.trim() === ''

  const parsedJobIds = useMemo(
    () =>
      useFilter || mode === 'submit'
        ? []
        : jobIdsText
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean),
    [useFilter, mode, jobIdsText]
  )

  const parsedFilter = useMemo<{
    error: string | null
    value: CampaignRerunTarget['filter']
  }>(() => {
    if (!useFilter) return { error: null, value: null }
    const trimmed = filterText.trim()
    if (!trimmed) return { error: '筛选条件不能为空', value: null }
    try {
      return { error: null, value: JSON.parse(trimmed) }
    } catch {
      return { error: '筛选条件不是合法 JSON', value: null }
    }
  }, [useFilter, filterText])

  const knobsValid =
    (watermarkText.trim() === '' || parsedKnobs.watermark != null) &&
    (batchSizeText.trim() === '' || parsedKnobs.batch_size != null)

  const rerunShapeValid =
    mode === 'submit' ||
    ((useFilter ? parsedFilter.error === null : parsedJobIds.length > 0) &&
      (mode !== 'rerun' || fromFailedNode || nodeKey.trim() !== ''))

  const submitShapeValid =
    mode !== 'submit' ||
    (submitChannel === 'inline'
      ? inlineCount > 0 && parsedInline?.error == null && !connectionKeyMissing
      : manifestFile != null)

  const targetValid = knobsValid && rerunShapeValid && submitShapeValid

  // 塑形输入的快照 key：模式 / 名称 / 清单来源与内容 / 连接 Key / 筛选 /
  // 节点 / 批参数——任一字段变化都构成新请求，旧试算结果随之失效。
  const previewKey = [
    mode,
    nameText.trim(),
    submitChannel,
    itemsText,
    connectionKey.trim(),
    manifestFile?.name ?? '',
    useFilter,
    filterText,
    jobIdsText,
    nodeKey.trim(),
    fromFailedNode,
    watermarkText,
    batchSizeText,
  ].join('\u0000')
  const previewTokenRef = useRef(0)
  // 展示层派生：只有「当前输入的试算结果」可见（loading 期间也不展示旧值）。
  const previewVisible = previewLoading ? null : preview
  const previewResult =
    previewVisible?.key === previewKey ? previewVisible.result : null
  const previewError =
    previewVisible?.key === previewKey ? previewVisible.error : null

  const buildInlineSubmit = (): CampaignSubmitInlineTarget | null => {
    if (mode !== 'submit' || !parsedInline || parsedInline.items.length === 0) {
      return null
    }
    return {
      items: parsedInline.items,
      ...knobOverrides(),
    }
  }

  function knobOverrides() {
    return {
      ...(parsedKnobs.watermark != null
        ? { watermark: parsedKnobs.watermark }
        : {}),
      ...(parsedKnobs.batch_size != null
        ? { batch_size: parsedKnobs.batch_size }
        : {}),
    }
  }

  const buildRequest = (): CampaignPreviewRequest => {
    const submit = buildInlineSubmit()
    if (mode === 'submit' && submit) {
      return { mode, name: nameText.trim(), submit }
    }
    const rerun: CampaignRerunTarget = {
      from_failed_node: mode === 'rerun' ? fromFailedNode : false,
      ...(mode === 'rerun' && nodeKey.trim() !== '' && !fromFailedNode
        ? { node_key: nodeKey.trim() }
        : {}),
      ...(useFilter
        ? { filter: parsedFilter.value ?? undefined }
        : { job_ids: parsedJobIds }),
      ...knobOverrides(),
    }
    return { mode, name: nameText.trim(), rerun }
  }

  const runPreview = async () => {
    // 请求令牌 + 快照：响应回来时若已有更新的请求则丢弃；写入的结果
    // 绑定发起时的快照，输入随后再变也会在展示层被过滤掉。
    const token = ++previewTokenRef.current
    const requestKey = previewKey
    setPreviewLoading(true)
    try {
      const response = await previewCampaign(workspaceId, buildRequest())
      if (token !== previewTokenRef.current) return
      setPreview({ key: requestKey, result: response.result, error: null })
    } catch (err) {
      if (token !== previewTokenRef.current) return
      setPreview({
        key: requestKey,
        result: null,
        error: err instanceof Error ? err.message : '试算失败',
      })
    } finally {
      if (token === previewTokenRef.current) setPreviewLoading(false)
    }
  }

  const handleCreate = async () => {
    setSubmitting(true)
    try {
      const name = nameText.trim()
      if (mode === 'submit' && submitChannel === 'upload' && manifestFile) {
        await createCampaignFromManifest(workspaceId, manifestFile, {
          ...(name ? { name } : {}),
          ...knobOverrides(),
        })
      } else if (mode === 'submit') {
        const submit = buildInlineSubmit()
        if (!submit) throw new Error('清单为空')
        await createSubmitCampaign(workspaceId, submit, name)
      } else {
        const request = buildRequest()
        if (request.rerun) {
          await createCampaign(
            workspaceId,
            request.mode as 'rerun' | 'upgrade',
            {
              ...request.rerun,
            },
            name
          )
        }
      }
      showToast('批量任务已创建，将按执行节奏自动分批执行', 'success')
      onCreated()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '创建失败', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  const canAdvance = step === 0 || (step === 1 && targetValid)

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>新建批量任务</DialogTitle>
      <DialogContent>
        <Stepper
          activeStep={step}
          sx={{ my: 2 }}
          data-testid="batch-wizard-stepper"
        >
          {STEPS.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {step === 0 && (
          <Stack spacing={2}>
            <TextField
              select
              label="任务类型"
              value={mode}
              onChange={(event) => setMode(event.target.value as WizardMode)}
              data-testid="batch-wizard-mode"
            >
              <MenuItem value="submit">添加任务</MenuItem>
              <MenuItem value="rerun">重跑任务</MenuItem>
              <MenuItem value="upgrade">升级任务</MenuItem>
            </TextField>
            <Alert severity="info">{MODE_HINTS[mode]}</Alert>
            <TextField
              label="任务名称（可选）"
              value={nameText}
              onChange={(event) => setNameText(event.target.value)}
              placeholder={`如「${defaultName(mode)}」`}
              helperText="留空自动按类型和时间命名"
              data-testid="batch-wizard-name"
            />
          </Stack>
        )}

        {step === 1 && (
          <Stack spacing={2}>
            {mode === 'submit' ? (
              <>
                <TextField
                  select
                  label="清单来源"
                  value={submitChannel}
                  onChange={(event) =>
                    setSubmitChannel(event.target.value as SubmitChannel)
                  }
                  data-testid="batch-wizard-channel"
                >
                  <MenuItem value="inline">粘贴 ID（一行一个）</MenuItem>
                  <MenuItem value="upload">
                    上传清单文件（.jsonl / .csv）
                  </MenuItem>
                </TextField>
                {submitChannel === 'inline' ? (
                  <>
                    <TextField
                      label="连接 Key（外部 ID 通道）"
                      value={connectionKey}
                      onChange={(event) => setConnectionKey(event.target.value)}
                      error={connectionKeyMissing}
                      helperText={
                        connectionKeyMissing
                          ? '粘贴的是纯 ID，需要先填写连接 Key 才能关联外部数据源'
                          : '外部平台条目的连接标识；JSON 行格式自带时不适用'
                      }
                      data-testid="batch-wizard-connection-key"
                    />
                    <TextField
                      multiline
                      rows={8}
                      label={`外部 ID（每行一个，当前 ${inlineCount.toLocaleString()} 行）`}
                      placeholder={'Q-1001\nQ-1002\nQ-1003'}
                      value={itemsText}
                      onChange={(event) => setItemsText(event.target.value)}
                      error={Boolean(parsedInline?.error)}
                      helperText={
                        parsedInline?.error ??
                        (inlineCount > 0
                          ? `已解析 ${inlineCount.toLocaleString()} 条；任务会按执行节奏自动分批创建`
                          : '任务会按执行节奏自动分批创建，进度在「批量任务」页可见')
                      }
                      data-testid="batch-wizard-items-input"
                    />
                  </>
                ) : (
                  <>
                    <Button variant="outlined" component="label">
                      选择清单文件
                      <input
                        type="file"
                        accept=".jsonl,.csv,.txt"
                        hidden
                        data-testid="batch-wizard-manifest-input"
                        onChange={(event) => {
                          setManifestFile(event.target.files?.[0] ?? null)
                          event.target.value = ''
                        }}
                      />
                    </Button>
                    {manifestFile && (
                      <Alert severity="info">
                        已选择 {manifestFile.name}（文件通道不支持试算，
                        服务端创建时统一校验）
                      </Alert>
                    )}
                  </>
                )}
              </>
            ) : (
              <>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={useFilter}
                      onChange={(event) => setUseFilter(event.target.checked)}
                    />
                  }
                  label="按筛选条件全量（否则指定任务 ID）"
                />
                {useFilter ? (
                  <TextField
                    multiline
                    rows={6}
                    label="筛选条件（JSON）"
                    placeholder={'{"status": "failed"}'}
                    value={filterText}
                    onChange={(event) => setFilterText(event.target.value)}
                    error={Boolean(parsedFilter.error)}
                    helperText={
                      parsedFilter.error ??
                      '字段同任务列表筛选（status / search / workflow_version 等）'
                    }
                    data-testid="batch-wizard-filter-input"
                  />
                ) : (
                  <TextField
                    multiline
                    rows={6}
                    label="任务 ID（一行一个）"
                    value={jobIdsText}
                    onChange={(event) => setJobIdsText(event.target.value)}
                    helperText={
                      parsedJobIds.length > 0
                        ? `已解析 ${parsedJobIds.length} 个`
                        : undefined
                    }
                    data-testid="batch-wizard-jobids-input"
                  />
                )}
                {mode === 'rerun' && (
                  <>
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={fromFailedNode}
                          onChange={(event) =>
                            setFromFailedNode(event.target.checked)
                          }
                        />
                      }
                      label="从失败节点重跑"
                    />
                    <TextField
                      label="从指定节点重跑（可选）"
                      value={nodeKey}
                      onChange={(event) => setNodeKey(event.target.value)}
                      disabled={fromFailedNode}
                      helperText="与「从失败节点」二选一"
                      data-testid="batch-wizard-nodekey-input"
                    />
                  </>
                )}
              </>
            )}
            <TextField
              label="队列水位线（可选）"
              value={watermarkText}
              onChange={(event) => setWatermarkText(event.target.value)}
              error={
                watermarkText.trim() !== '' && parsedKnobs.watermark == null
              }
              helperText="执行队列中的任务数到达该值时暂停投放；留空使用默认值"
              inputProps={{ inputMode: 'numeric' }}
              data-testid="batch-wizard-watermark-input"
            />
            <TextField
              label="每批数量（可选）"
              value={batchSizeText}
              onChange={(event) => setBatchSizeText(event.target.value)}
              error={
                batchSizeText.trim() !== '' && parsedKnobs.batch_size == null
              }
              helperText="每批投放的目标数；留空使用默认值"
              inputProps={{ inputMode: 'numeric' }}
              data-testid="batch-wizard-batchsize-input"
            />
          </Stack>
        )}

        {step === 2 && (
          <Stack spacing={2}>
            <Alert severity="info">
              {previewSummary(previewResult) ?? '可先试算确认数量，再创建。'}
            </Alert>
            <Button
              variant="outlined"
              onClick={() => void runPreview()}
              disabled={
                !targetValid ||
                (mode === 'submit' && submitChannel === 'upload') ||
                previewLoading
              }
              data-testid="batch-wizard-preview-button"
            >
              {previewLoading ? '试算中...' : '试算'}
            </Button>
            {previewError && <Alert severity="error">{previewError}</Alert>}
            <Alert severity="info">
              创建后立即开始投放；可随时暂停/恢复。异常终止后重新创建即可续投，
              已处理的目标自动跳过、不会重复。
            </Alert>
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={submitting}>
          取消
        </Button>
        {step > 0 && (
          <Button onClick={() => setStep(step - 1)} disabled={submitting}>
            上一步
          </Button>
        )}
        {step < 2 ? (
          <Button
            variant="contained"
            onClick={() => setStep(step + 1)}
            disabled={!canAdvance}
            data-testid="batch-wizard-next"
          >
            下一步
          </Button>
        ) : (
          <Button
            variant="contained"
            onClick={() => void handleCreate()}
            disabled={submitting || !targetValid}
            data-testid="batch-wizard-create"
          >
            {submitting ? '创建中...' : '创建批量任务'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

function defaultName(mode: WizardMode): string {
  if (mode === 'submit') return '添加 · 新一批任务'
  if (mode === 'rerun') return '重跑 · 失败任务'
  return '升级 · 存量任务'
}

/** 解析行内 jsonl 为 submit items（宽松 JSON Lines：逐行 parse）。 */
function parseJsonlItems(
  text: string,
  extraItems: CampaignSubmitInlineTarget['items'] = []
): {
  items: CampaignSubmitInlineTarget['items']
  error: string | null
} {
  const items: CampaignSubmitInlineTarget['items'] = [...extraItems]
  for (const [index, line] of text.split('\n').entries()) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      const parsed = JSON.parse(trimmed)
      const isObj =
        typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      if (isObj) {
        items.push(parsed as CampaignSubmitInlineTarget['items'][number])
      } else {
        return { items, error: `第 ${index + 1} 行不是 JSON 对象` }
      }
    } catch {
      return { items, error: `第 ${index + 1} 行不是合法 JSON` }
    }
  }
  return { items, error: null }
}

function previewSummary(
  result: CampaignPreviewResponse['result'] | null
): string | null {
  if (!result) return null
  if (result.mode === 'submit') {
    return `试算结果：共 ${result.total_items} 条，将新建 ${result.would_create} 条，跳过（已存在）${result.would_skip} 条`
  }
  return `试算结果：匹配 ${result.total_count} 个任务，可执行 ${result.eligible_count} 个`
}
