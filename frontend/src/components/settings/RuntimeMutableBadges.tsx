import type { ConfigSchema } from '../../types'

// 运行开关（runtime_mutable）键在 intake 时不冻结，每次 dispatch 按
// workspace 覆盖实时重取——用徽标把这些键标出来，解释为什么它们的
// 改动即时生效、其他键被 intake 冻结。
export function RuntimeMutableBadges({ schema }: { schema: ConfigSchema }) {
  const keys = Object.entries(schema.properties ?? {})
    .filter(([, prop]) => prop.runtime_mutable === true)
    .map(([key]) => key)
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
      {keys.map((key) => (
        <span
          key={key}
          title="运行开关：intake 时不冻结，每次 dispatch 按 workspace 节点配置实时重取"
          style={{
            fontSize: 11,
            color: '#1565c0',
            background: '#e3f2fd',
            borderRadius: 4,
            padding: '2px 6px',
          }}
        >
          {key} · 运行开关
        </span>
      ))}
      <span style={{ fontSize: 11, color: '#616161' }}>
        运行开关改动即时生效；其他键在 job intake 时冻结。
      </span>
    </div>
  )
}
