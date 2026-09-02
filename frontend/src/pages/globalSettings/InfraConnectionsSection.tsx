import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import {
  getInfraConnections,
  testInfraConnection,
} from '../../api/infraConnections'
import type {
  InfraConnectionsResponse,
  InfraConnectionTarget,
} from '../../api/infraConnections'
import styles from '../GlobalSettingsPage.module.css'
import localStyles from './InfraConnectionsSection.module.css'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

type BadgeTone = 'ok' | 'fail' | 'muted'

const BADGE_CLASS: Record<BadgeTone, string> = {
  ok: localStyles.badgeOk,
  fail: localStyles.badgeFail,
  muted: localStyles.badgeMuted,
}

function StatusBadge({ tone, label }: { tone: BadgeTone; label: string }) {
  return (
    <span className={`${localStyles.badge} ${BADGE_CLASS[tone]}`}>{label}</span>
  )
}

interface TestState {
  testing: boolean
  result: { ok: boolean; message: string } | null
}

const INITIAL_TEST_STATE: TestState = { testing: false, result: null }

function useConnectionTest(target: InfraConnectionTarget, label: string) {
  const [state, setState] = useState<TestState>(INITIAL_TEST_STATE)

  async function run() {
    setState({ testing: true, result: null })
    try {
      const outcome = await testInfraConnection(target)
      const message = outcome.ok
        ? `${label}连接正常`
        : `${label}连接失败：${outcome.reason ?? '未知原因'}`
      setState({ testing: false, result: { ok: outcome.ok, message } })
      useUiStore.getState().showToast(message, outcome.ok ? 'success' : 'error')
    } catch (err) {
      const message = `${label}连接测试请求失败：${errorMessage(err)}`
      setState({ testing: false, result: { ok: false, message } })
      useUiStore.getState().showToast(message, 'error')
    }
  }

  return { ...state, run }
}

function TestButton({
  target,
  label,
}: {
  target: InfraConnectionTarget
  label: string
}) {
  const { testing, result, run } = useConnectionTest(target, label)
  return (
    <>
      <button
        type="button"
        className={styles.textButton}
        aria-label={`测试${label}连接`}
        disabled={testing}
        onClick={() => void run()}
      >
        {testing ? '测试中…' : '测试连接'}
      </button>
      {result && (
        <p className={result.ok ? localStyles.okText : localStyles.failText}>
          {result.message}
        </p>
      )}
    </>
  )
}

function DatabaseBlock({
  database,
}: {
  database: InfraConnectionsResponse['database']
}) {
  return (
    <div>
      <p className={styles.groupTitle}>数据库</p>
      <dl className={localStyles.kvGrid}>
        <dt>引擎</dt>
        <dd>{database.engine}</dd>
        <dt>地址</dt>
        <dd>
          {database.host}
          {database.port !== null ? `:${database.port}` : ''}
        </dd>
        <dt>数据库名</dt>
        <dd>{database.name || '—'}</dd>
        <dt>用户</dt>
        <dd>{database.user || '—'}</dd>
        <dt>密码</dt>
        <dd>{database.password_set ? '已设置（不回显）' : '未设置'}</dd>
        <dt>连接 URL</dt>
        <dd>
          <code>{database.masked_url}</code>
        </dd>
      </dl>
      <TestButton target="database" label="数据库" />
    </div>
  )
}

const CREDENTIALS_LABEL: Record<string, string> = {
  static: '静态凭据（已设置，不回显）',
  'default-chain': '默认凭据链（实例角色等）',
  unconfigured: '未配置',
}

function StorageBlock({
  storage,
}: {
  storage: InfraConnectionsResponse['storage']
}) {
  return (
    <div>
      <p className={styles.groupTitle}>
        对象存储
        {!storage.configured && <StatusBadge tone="muted" label="未配置" />}
        {storage.configured && storage.reachable && (
          <StatusBadge tone="ok" label="正常" />
        )}
        {storage.configured && !storage.reachable && (
          <StatusBadge tone="fail" label="不可达" />
        )}
      </p>
      {!storage.configured && (
        <p className={styles.hint}>
          未配置对象存储（AGENT_LEGION_S3_BUCKET
          未设置）；材料与产物对象存储不可用。
        </p>
      )}
      {storage.configured && (
        <dl className={localStyles.kvGrid}>
          <dt>服务类型</dt>
          <dd>{storage.backend}</dd>
          <dt>Endpoint</dt>
          <dd>{storage.endpoint_url || 'AWS S3（默认端点）'}</dd>
          <dt>公开 Endpoint</dt>
          <dd>{storage.public_endpoint_url || '与 Endpoint 相同'}</dd>
          <dt>Bucket</dt>
          <dd>{storage.bucket}</dd>
          <dt>Region</dt>
          <dd>{storage.region}</dd>
          <dt>凭据</dt>
          <dd>
            {CREDENTIALS_LABEL[storage.credentials] ?? storage.credentials}
          </dd>
        </dl>
      )}
      <TestButton target="storage" label="对象存储" />
    </div>
  )
}

export function InfraConnectionsSection() {
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.infraConnections(),
    queryFn: getInfraConnections,
  })
  const loadError = toErrorMessage(loadQueryError)

  if (loadError) {
    return (
      <div className={styles.card}>
        <h3 className={styles.heading}>基础设施连接</h3>
        <p className={styles.error} role="alert">
          {loadError}
        </p>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>基础设施连接</h3>
      <p className={styles.hint}>
        实例级数据库与对象存储连接信息（只读，经环境变量配置）；凭据绝不回显。
      </p>
      <DatabaseBlock database={data.database} />
      <StorageBlock storage={data.storage} />
    </div>
  )
}
