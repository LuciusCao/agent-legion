// #471 冷启动容量爬坡：worker 控制台的纯函数面（无 DOM），
// 由 app.js 以 ES module 引入、app.test.mjs（node:test）直接测试。
// 与 worker/ramp_up.py 的 RampUpControls 默认值对齐。

// 爬坡表单默认值：initial/step/interval_seconds 三键。
export const RAMP_UP_DEFAULTS = { initial: 1, step: 1, interval_seconds: 60 };

// 概览进度行文本：不在爬坡（null、已到顶、未首观察的 0 档）返回空串，
// 调用方据此隐藏。
export function rampUpLine(ramp) {
  if (!ramp || !Number.isFinite(ramp.effective) || !Number.isFinite(ramp.target)) return "";
  if (ramp.effective >= ramp.target || ramp.effective <= 0) return "";
  const next = Number.isFinite(ramp.next_tier_seconds)
    ? ` · ${Math.max(0, Math.round(ramp.next_tier_seconds))}s 后 +1 档`
    : "";
  return `容量爬坡中 ${ramp.effective} / ${ramp.target}${next}`;
}

// 表单 → PUT payload 的 ramp_up 值：未勾选提交 null（禁用 = 一次性全量）；
// 勾选才提交块，留空字段回退默认值。data 需 FormData 语义（缺失键 get 返回 null）。
export function rampUpFromForm(data) {
  if (!data.get("ramp_up_enabled")) return null;
  const block = {};
  for (const key of Object.keys(RAMP_UP_DEFAULTS)) {
    const raw = data.get(`ramp_up_${key}`);
    block[key] = raw === null || raw === "" ? RAMP_UP_DEFAULTS[key] : Number(raw);
  }
  return block;
}

// ramp_up 配置块 → 表单控件值：启用勾选 + 三个数字输入（null = 禁用态，
// 缺省键补默认）。set(key, value) 由调用方绑定到真实 DOM 控件。
export function rampUpFormValues(block) {
  const values = { ...RAMP_UP_DEFAULTS, ...(block || {}) };
  return { enabled: Boolean(block), values };
}
