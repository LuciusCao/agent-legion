import { useEffect, useState } from 'react'

import { useWorkspaceStore } from '../stores/workspaceStore'
import { fetchPipelineDefinition } from '../api'
import type { PipelineDefinitionRecord } from '../types'

type Props = {
  isVideoHive: boolean
}

type ResourceForm = {
  enabled: boolean
  api_url: string
  bank_version: string
  country_id: string
  subject_id: string
  page_size: string
}

type ResourcesForm = {
  by_knowledge: ResourceForm
  question_detail: ResourceForm
}

type IntakeConfigForm = {
  defaultEntity: string
  enabledModes: string[]
  labelOverrides: Record<string, string>
}

function isIntakeModeSupported(entity: string, modeKey: string): boolean {
  if (entity === 'question') {
    return modeKey === 'direct_ids' || modeKey === 'by_knowledge'
  }
  if (entity === 'video') {
    return modeKey === 'direct_ids'
  }
  return false
}

function valueFromConfig(
  config: Record<string, unknown> | undefined,
  key: string
) {
  const value = config?.[key]
  return typeof value === 'string' ? value : ''
}

function resourceBindingConfig(
  config: Record<string, unknown> | undefined,
  key: string
): Record<string, unknown> {
  const resources = config?.resources
  if (!resources || typeof resources !== 'object') return {}
  const binding = (resources as Record<string, unknown>)[key]
  if (!binding || typeof binding !== 'object') return {}
  const bindingConfig = (binding as Record<string, unknown>).config
  return bindingConfig && typeof bindingConfig === 'object'
    ? (bindingConfig as Record<string, unknown>)
    : {}
}

function hasResourceBinding(
  config: Record<string, unknown> | undefined,
  key: string
) {
  const resources = config?.resources
  return Boolean(
    resources &&
    typeof resources === 'object' &&
    (resources as Record<string, unknown>)[key]
  )
}

function initialResourceForm(
  resourceConfig: Record<string, unknown> | undefined,
  cmsConfig: Record<string, unknown> | undefined,
  resourceKey: string,
  legacyUrlKey: string,
  defaults: Partial<ResourceForm> = {}
): ResourceForm {
  const config = resourceBindingConfig(resourceConfig, resourceKey)
  return {
    enabled:
      hasResourceBinding(resourceConfig, resourceKey) ||
      Boolean(valueFromConfig(cmsConfig, legacyUrlKey)),
    api_url:
      valueFromConfig(config, 'api_url') ||
      valueFromConfig(cmsConfig, legacyUrlKey) ||
      '',
    bank_version:
      valueFromConfig(config, 'bank_version') ||
      valueFromConfig(cmsConfig, 'bank_version') ||
      defaults.bank_version ||
      '',
    country_id:
      valueFromConfig(config, 'country_id') ||
      valueFromConfig(cmsConfig, 'country_id') ||
      defaults.country_id ||
      '',
    subject_id:
      valueFromConfig(config, 'subject_id') ||
      valueFromConfig(cmsConfig, 'subject_id') ||
      defaults.subject_id ||
      '',
    page_size: valueFromConfig(config, 'page_size') || defaults.page_size || '',
  }
}

function resourceFormToConfig(form: ResourcesForm): Record<string, unknown> {
  const resources: Record<string, unknown> = {}
  if (form.by_knowledge.enabled) {
    const cfg: Record<string, string> = {
      bank_version: form.by_knowledge.bank_version.trim(),
      country_id: form.by_knowledge.country_id.trim(),
      subject_id: form.by_knowledge.subject_id.trim(),
      page_size: form.by_knowledge.page_size.trim(),
    }
    if (form.by_knowledge.api_url.trim()) {
      cfg.api_url = form.by_knowledge.api_url.trim()
    }
    resources.by_knowledge = {
      provider: 'cms.question.list_by_knowledge',
      config: cfg,
    }
  }
  if (form.question_detail.enabled) {
    const cfg: Record<string, string> = {
      bank_version: form.question_detail.bank_version.trim(),
      country_id: form.question_detail.country_id.trim(),
      subject_id: form.question_detail.subject_id.trim(),
    }
    if (form.question_detail.api_url.trim()) {
      cfg.api_url = form.question_detail.api_url.trim()
    }
    resources.question_detail = {
      provider: 'cms.question.detail',
      config: cfg,
    }
  }
  return { resources }
}

export default function WorkspaceResources({ isVideoHive }: Props) {
  const { currentWorkspace } = useWorkspaceStore()

  if (isVideoHive) {
    return (
      <div>
        <h3>资源配置</h3>
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          Video Hive 资源配置由旧流程管理。
        </p>
      </div>
    )
  }

  if (!currentWorkspace) {
    return (
      <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
        请选择一个 workspace。
      </p>
    )
  }

  return <WorkspaceResourcesForm key={currentWorkspace.id} />
}

function WorkspaceResourcesForm() {
  const { currentWorkspace, updateWorkspace } = useWorkspaceStore()
  const resourceConfig = currentWorkspace?.resource_config
  const cmsConfig = currentWorkspace?.cms_config
  const [form, setForm] = useState<ResourcesForm>({
    by_knowledge: initialResourceForm(
      resourceConfig,
      cmsConfig,
      'by_knowledge',
      'question_list_url',
      { page_size: '50' }
    ),
    question_detail: initialResourceForm(
      resourceConfig,
      cmsConfig,
      'question_detail',
      'question_detail_url'
    ),
  })
  const initialIntakeConfig: IntakeConfigForm = (() => {
    const config = currentWorkspace?.intake_config || {}
    const enabledModes = Array.isArray(config.enabled_modes)
      ? config.enabled_modes
      : []
    return {
      defaultEntity: currentWorkspace?.default_entity || 'question',
      enabledModes,
      labelOverrides: config.label_overrides || {},
    }
  })()

  const [pipeline, setPipeline] = useState<PipelineDefinitionRecord | null>(
    null
  )
  const [intakeForm, setIntakeForm] =
    useState<IntakeConfigForm>(initialIntakeConfig)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!currentWorkspace?.default_pipeline_key) return
    fetchPipelineDefinition(currentWorkspace.default_pipeline_key)
      .then((response) => {
        setPipeline(response.pipeline)
        const storedModes = currentWorkspace.intake_config?.enabled_modes
        if (!Array.isArray(storedModes)) {
          const allSupportedModes =
            response.pipeline.intake?.modes
              .filter((mode) =>
                isIntakeModeSupported(
                  initialIntakeConfig.defaultEntity,
                  mode.key
                )
              )
              .map((mode) => mode.key) || []
          setIntakeForm((current) => ({
            ...current,
            enabledModes: allSupportedModes,
          }))
        }
      })
      .catch(() => setPipeline(null))
  }, [currentWorkspace, initialIntakeConfig.defaultEntity])

  function updateDefaultEntity(defaultEntity: string) {
    setIntakeForm((current) => {
      const supportedModes =
        pipeline?.intake?.modes
          .filter((mode) => isIntakeModeSupported(defaultEntity, mode.key))
          .map((mode) => mode.key) || []
      const enabledModes = current.enabledModes.filter((modeKey) =>
        supportedModes.includes(modeKey)
      )
      return {
        ...current,
        defaultEntity,
        enabledModes: enabledModes.length > 0 ? enabledModes : supportedModes,
      }
    })
  }

  function updateResourceField(
    resourceKey: keyof ResourcesForm,
    fieldKey: keyof ResourceForm,
    value: string
  ) {
    setForm((current) => ({
      ...current,
      [resourceKey]: {
        ...current[resourceKey],
        [fieldKey]: value,
      },
    }))
  }

  function toggleResource(resourceKey: keyof ResourcesForm) {
    setForm((current) => ({
      ...current,
      [resourceKey]: {
        ...current[resourceKey],
        enabled: !current[resourceKey].enabled,
      },
    }))
  }

  async function handleSave() {
    if (!currentWorkspace) return
    if (intakeForm.enabledModes.length === 0) {
      setError('至少启用一种 intake mode')
      return
    }
    setSaving(true)
    setMessage('')
    setError('')
    try {
      await updateWorkspace(currentWorkspace.id, {
        resource_config: resourceFormToConfig(form),
        default_entity: intakeForm.defaultEntity,
        intake_config: {
          enabled_modes: intakeForm.enabledModes,
          label_overrides: Object.fromEntries(
            Object.entries(intakeForm.labelOverrides).filter(([modeKey]) =>
              intakeForm.enabledModes.includes(modeKey)
            )
          ),
        },
      })
      setMessage('配置已保存')
    } catch {
      setError('保存配置失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 960 }}>
      <h3>题库接口配置</h3>
      <p
        style={{
          color: 'var(--md-sys-color-on-surface-variant)',
          marginTop: 4,
        }}
      >
        当前 workspace 会优先使用这里的配置；未填写的认证信息仍沿用全局 CMS
        配置。
      </p>

      <div style={{ display: 'grid', gap: 16, marginTop: 20 }}>
        <ResourceCard
          title="知识点下题目列表"
          provider="cms.question.list_by_knowledge"
          description="从一个知识点 code 展开出待生产的一批题目。"
          form={form.by_knowledge}
          pageSize
          onToggle={() => toggleResource('by_knowledge')}
          onChange={(field, value) =>
            updateResourceField('by_knowledge', field, value)
          }
        />
        <ResourceCard
          title="题目详情"
          provider="cms.question.detail"
          description="为每个题目生产任务拉取题干、选项、答案和解析上下文。"
          form={form.question_detail}
          onToggle={() => toggleResource('question_detail')}
          onChange={(field, value) =>
            updateResourceField('question_detail', field, value)
          }
        />
      </div>

      <section className="card-outlined" style={{ marginTop: 24, padding: 16 }}>
        <h3>Intake 配置</h3>

        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', marginBottom: 8 }}>
            处理对象 (Entity)
          </label>
          <md-outlined-select
            aria-label="处理对象 (Entity)"
            value={intakeForm.defaultEntity}
            onInput={(e: Event) =>
              updateDefaultEntity((e.target as HTMLSelectElement).value)
            }
          >
            <md-select-option value="question">
              <div slot="headline">题目</div>
            </md-select-option>
            <md-select-option value="video">
              <div slot="headline">视频</div>
            </md-select-option>
          </md-outlined-select>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', marginBottom: 8 }}>
            启用的 Intake Modes
          </label>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              marginTop: 8,
            }}
          >
            {pipeline?.intake?.modes.map((mode) =>
              (() => {
                const supported = isIntakeModeSupported(
                  intakeForm.defaultEntity,
                  mode.key
                )
                return (
                  <md-checkbox
                    key={mode.key}
                    aria-label={mode.label}
                    checked={
                      intakeForm.enabledModes.includes(mode.key) || undefined
                    }
                    disabled={!supported || undefined}
                    onClick={() => {
                      if (!supported) return
                      const next = intakeForm.enabledModes.includes(mode.key)
                        ? intakeForm.enabledModes.filter((k) => k !== mode.key)
                        : [...intakeForm.enabledModes, mode.key]
                      setIntakeForm({ ...intakeForm, enabledModes: next })
                    }}
                  >
                    {mode.label}
                  </md-checkbox>
                )
              })()
            )}
          </div>
        </div>

        {intakeForm.enabledModes.map((modeKey) => {
          const mode = pipeline?.intake?.modes.find((m) => m.key === modeKey)
          if (!mode) return null
          return (
            <md-outlined-text-field
              key={modeKey}
              aria-label={`${mode.label} 显示名称`}
              label={`${mode.label} 显示名称`}
              value={intakeForm.labelOverrides[modeKey] || ''}
              placeholder={mode.label}
              style={{ marginBottom: 12, display: 'block' }}
              onInput={(e: Event) => {
                const value = (e.target as HTMLInputElement).value
                setIntakeForm({
                  ...intakeForm,
                  labelOverrides: {
                    ...intakeForm.labelOverrides,
                    [modeKey]: value,
                  },
                })
              }}
            />
          )
        })}
      </section>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginTop: 20,
        }}
      >
        <md-filled-button onClick={handleSave} disabled={saving || undefined}>
          {saving ? '保存中…' : '保存配置'}
        </md-filled-button>
        {message ? (
          <span style={{ color: 'var(--md-sys-color-primary)' }}>
            {message}
          </span>
        ) : null}
        {error ? (
          <span style={{ color: 'var(--md-sys-color-error)' }}>{error}</span>
        ) : null}
      </div>
    </div>
  )
}

type ResourceCardProps = {
  title: string
  provider: string
  description: string
  form: ResourceForm
  pageSize?: boolean
  onToggle: () => void
  onChange: (field: keyof ResourceForm, value: string) => void
}

function ResourceCard({
  title,
  provider,
  description,
  form,
  pageSize = false,
  onToggle,
  onChange,
}: ResourceCardProps) {
  return (
    <section className="card-outlined" style={{ padding: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ minWidth: 0 }}>
          <h4 style={{ margin: 0, fontSize: 16 }}>{title}</h4>
          <p
            style={{
              color: 'var(--md-sys-color-on-surface-variant)',
              marginTop: 4,
            }}
          >
            {description}
          </p>
          <div style={{ marginTop: 8 }}>
            <md-assist-chip label={provider} />
          </div>
        </div>
        <md-checkbox
          aria-label={`启用${title}`}
          checked={form.enabled || undefined}
          onClick={onToggle}
        />
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
          marginTop: 16,
          opacity: form.enabled ? 1 : 0.6,
        }}
      >
        <md-outlined-text-field
          label="API URL"
          aria-label={`${title} API URL`}
          value={form.api_url}
          disabled={!form.enabled || undefined}
          onInput={(event: Event) =>
            onChange('api_url', (event.target as HTMLInputElement).value)
          }
        />
        <md-outlined-text-field
          label="题库版本"
          aria-label={`${title} 题库版本`}
          value={form.bank_version}
          disabled={!form.enabled || undefined}
          onInput={(event: Event) =>
            onChange('bank_version', (event.target as HTMLInputElement).value)
          }
        />
        <md-outlined-text-field
          label="国家 ID"
          aria-label={`${title} 国家 ID`}
          value={form.country_id}
          disabled={!form.enabled || undefined}
          onInput={(event: Event) =>
            onChange('country_id', (event.target as HTMLInputElement).value)
          }
        />
        <md-outlined-text-field
          label="学科 ID"
          aria-label={`${title} 学科 ID`}
          value={form.subject_id}
          disabled={!form.enabled || undefined}
          onInput={(event: Event) =>
            onChange('subject_id', (event.target as HTMLInputElement).value)
          }
        />
        {pageSize ? (
          <md-outlined-text-field
            label="每页数量"
            aria-label={`${title} 每页数量`}
            value={form.page_size}
            disabled={!form.enabled || undefined}
            onInput={(event: Event) =>
              onChange('page_size', (event.target as HTMLInputElement).value)
            }
          />
        ) : null}
      </div>
    </section>
  )
}
