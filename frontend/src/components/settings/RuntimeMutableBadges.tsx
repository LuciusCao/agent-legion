import type { ConfigSchema } from '../../types'

// 运行开关（runtime_mutable）键在 intake 时不冻结，每次 dispatch 按
// workspace 覆盖实时重取——用徽标把这些键标出来，解释为什么它们的
// 改动即时生效、其他键被 intake 冻结。#691：平台保留执行键
// timeout_seconds 固定为运行时可调（dispatch / Worker claim 时重解析，
// 排队中尚未开始的节点即用新值），同样标出；sandbox_network 仍随
// intake 冻结（网络出站是安全边界）。
const RUNTIME_ADJUSTABLE_RESERVED_KEYS = ['timeout_seconds']

export function RuntimeMutableBadges({ schema }: { schema: ConfigSchema }) {
  const properties = schema.properties ?? {}
  const keys = Object.entries(properties)
    .filter(([, prop]) => prop.runtime_mutable === true)
    .map(([key]) => ({ key, label: '运行开关' }))
  for (const key of RUNTIME_ADJUSTABLE_RESERVED_KEYS) {
    if (key in properties) keys.push({ key, label: '运行时可调' })
  }
  if (keys.length === 0) return null
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        alignItems: 'center',
        marginBottom: 12,
      }}
    >
      {keys.map(({ key, label }) => (
        <span
          key={key}
          title={`${label}：intake 时不冻结，每次 dispatch 按 workspace 节点配置实时重取`}
          style={{
            fontSize: 11,
            color: '#1565c0',
            background: '#e3f2fd',
            borderRadius: 4,
            padding: '2px 6px',
          }}
        >
          {key} · {label}
        </span>
      ))}
      <span style={{ fontSize: 11, color: '#616161' }}>
        标注的键改动即时生效（排队中尚未开始的节点即用新值，已开始的执行不受影响）；其他键（含
        sandbox_network）在 job intake 时冻结。
      </span>
    </div>
  )
}
