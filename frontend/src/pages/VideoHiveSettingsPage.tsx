import { useEffect, useState } from 'react'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { useUiStore } from '../stores/uiStore'
import { api } from '../api'
import type { GlobalServiceStatus } from '../types'

type VideoHiveConfig = {
  asr: {
    provider: string
    whisperConfigured: boolean
    sensevoiceConfigured: boolean
    vadEnabled: boolean
  }
  openclaw: {
    runnerCount: number
    timeoutSeconds: number
  }
}

export function VideoHiveSettingsPage() {
  const { workerPaused, fetchWorkerStatus, setWorkerPaused, showToast } =
    useUiStore()
  const [services, setServices] = useState<GlobalServiceStatus | null>(null)
  const [config, setConfig] = useState<VideoHiveConfig | null>(null)

  useEffect(() => {
    fetchWorkerStatus().catch(() => {})
    api<{ cms: { baseUrl: string; tokenConfigured: boolean; env: string } }>(
      '/api/global-services'
    )
      .then((data) =>
        setServices({
          cms: {
            baseUrl: data.cms.baseUrl,
            tokenConfigured: data.cms.tokenConfigured,
            env: data.cms.env,
            healthy: null,
            lastCheckedAt: null,
          },
        })
      )
      .catch(() => {})
    api<VideoHiveConfig>('/api/video-hive/config')
      .then((data) => setConfig(data))
      .catch(() => {})
  }, [fetchWorkerStatus])

  const togglePause = async () => {
    const next = !workerPaused
    try {
      await setWorkerPaused(next)
      showToast(next ? '已关闭自动调度' : '已开启自动调度', 'success')
    } catch {
      showToast('更新失败', 'error')
    }
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="Video Hive / 设置"
          backTo="/video-hive"
          scrolled={scrolled}
        />
      )}
      mainClassName="settings-main"
    >
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div className="card-outlined" style={{ marginBottom: 16 }}>
          <div
            style={{
              padding: 16,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              fontWeight: 500,
              fontSize: 16,
            }}
          >
            <md-icon>cloud</md-icon>
            <span>全局服务状态</span>
          </div>
          <div style={{ padding: '0 16px 16px' }}>
            {services ? (
              <div style={{ display: 'grid', gap: 12 }}>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    CMS Base URL
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {services.cms.baseUrl || '-'}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    Token 状态
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {services.cms.tokenConfigured ? '已配置' : '未配置'}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    环境
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {services.cms.env || '-'}
                  </div>
                </div>
              </div>
            ) : (
              <div>加载中…</div>
            )}
          </div>
        </div>

        <div className="card-outlined" style={{ marginBottom: 16 }}>
          <div
            style={{
              padding: 16,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              fontWeight: 500,
              fontSize: 16,
            }}
          >
            <md-icon>toggle_on</md-icon>
            <span>Worker 控制</span>
          </div>
          <div style={{ padding: '0 16px 16px' }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                fontSize: 14,
              }}
            >
              <span>自动调度</span>
              <md-switch
                selected={!workerPaused || undefined}
                onClick={togglePause}
              />
            </label>
          </div>
        </div>

        <div className="card-outlined" style={{ marginBottom: 16 }}>
          <div
            style={{
              padding: 16,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              fontWeight: 500,
              fontSize: 16,
            }}
          >
            <md-icon>stream</md-icon>
            <span>工作流信息</span>
          </div>
          <div style={{ padding: '0 16px 16px' }}>
            {config ? (
              <div style={{ display: 'grid', gap: 12 }}>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    ASR Provider
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.asr.provider}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    Whisper 配置
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.asr.whisperConfigured ? '已配置' : '未配置'}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    SenseVoice 配置
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.asr.sensevoiceConfigured ? '已配置' : '未配置'}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    VAD
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.asr.vadEnabled ? '已启用' : '未启用'}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    OpenClaw Runners
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.openclaw.runnerCount}
                  </div>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    超时时间
                  </span>
                  <div style={{ fontSize: 14, marginTop: 4 }}>
                    {config.openclaw.timeoutSeconds}s
                  </div>
                </div>
              </div>
            ) : (
              <div>加载中…</div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
