import { create } from 'zustand'

/** M65 拆卡：运行配置卡「已生效」落定态的会话内记录。卡在「应用并生效」
 * 成功后按 runtimeCardKey 登记产生的配置版本号与实际生效键——diff 随基线
 * 推进清空后卡片转入落定态（不消失），展示建议原文备查 + 版本历史入口。
 * 会话内内存态：页面重载后记录丢失，diff 已清空的卡回落 M48 语义（不再
 * 渲染），与旧行为一致。 */
export type RuntimeCardSettlement = {
  /** 本次生效追加的配置版本号；版本号读取失败时为 null（落定态不标版本）。 */
  version: number | null
  /** 应用时相对当时生效基线的 diff 键（落定态灰色原文行）。 */
  savedKeys: string[]
  /** 落定时刻（Date.now()），落定态 meta 行「已于 HH:MM:SS 生效」用。 */
  savedAt: number
}

interface RuntimeCardSettlementState {
  settled: Record<string, RuntimeCardSettlement>
  settleRuntimeCard: (key: string, settlement: RuntimeCardSettlement) => void
}

export const useRuntimeCardSettlementStore = create<RuntimeCardSettlementState>(
  (set) => ({
    settled: {},
    settleRuntimeCard: (key, settlement) =>
      set((state) => ({ settled: { ...state.settled, [key]: settlement } })),
  })
)

export function useRuntimeCardSettlement(
  key: string
): RuntimeCardSettlement | null {
  return useRuntimeCardSettlementStore((state) => state.settled[key] ?? null)
}
