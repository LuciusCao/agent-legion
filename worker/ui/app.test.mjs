// worker/ui 无构建无框架，直接用 Node 内置 node:test 跑纯函数测试：
//   node --test worker/ui/app.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import { NUMBER_DEFAULTS, executionLabel, formatElapsed, labelsFromText, linesFromText, modelsFromText, numberField } from "./app.js";

test("labelsFromText 解析多行 key=value", () => {
  assert.deepEqual(labelsFromText("host=home\nos=mac"), { host: "home", os: "mac" });
});

test("labelsFromText 跳过空行并裁剪空白", () => {
  assert.deepEqual(labelsFromText("\n  host = home \n\n"), { host: "home" });
});

test("labelsFromText 值里允许出现 =（按第一个 = 切分）", () => {
  assert.deepEqual(labelsFromText("query=a=b"), { query: "a=b" });
});

test("labelsFromText 空输入得到空对象", () => {
  assert.deepEqual(labelsFromText(""), {});
});

test("labelsFromText 拒绝缺少 = 或 key 为空的行", () => {
  assert.throws(() => labelsFromText("no-separator"), /key=value/);
  assert.throws(() => labelsFromText("=value-only"), /key=value/);
});

test("linesFromText 去空行并去重", () => {
  assert.deepEqual(linesFromText("gpu\n\n gpu \nreview"), ["gpu", "review"]);
});

test("modelsFromText 解析 provider/model 并保留模型路径", () => {
  assert.deepEqual(modelsFromText("openai/gpt-5\nvertex/google/gemini"), [
    { provider: "openai", model: "gpt-5" },
    { provider: "vertex", model: "google/gemini" },
  ]);
  assert.throws(() => modelsFromText("missing-model/"), /provider\/model/);
});

// FormData.get 对缺失键返回 null；用同样语义的桩代替 Map（Map.get 返回 undefined）。
const formDataLike = (values) => ({ get: (key) => (key in values ? values[key] : null) });

test("numberField 留空或缺失时回退到后端默认值", () => {
  assert.equal(numberField(formDataLike({ max_concurrency: "" }), "max_concurrency"), NUMBER_DEFAULTS.max_concurrency);
  assert.equal(numberField(formDataLike({}), "poll_interval_seconds"), NUMBER_DEFAULTS.poll_interval_seconds);
});

test("numberField 有值时转成数字", () => {
  assert.equal(numberField(formDataLike({ max_concurrency: "4" }), "max_concurrency"), 4);
});

test("formatElapsed 把 started_at 到 now 的间隔格式化为 mm:ss", () => {
  const now = Date.parse("2026-07-23T02:00:00Z");
  assert.equal(formatElapsed("2026-07-23T01:58:20Z", now), "01:40");
});

test("formatElapsed 坏时间或未来时间显示 00:00", () => {
  assert.equal(formatElapsed("not-a-date", 0), "00:00");
  assert.equal(formatElapsed("2026-07-23T02:00:00Z", Date.parse("2026-07-23T01:00:00Z")), "00:00");
});

test("executionLabel 取 agent · 节点，缺省时回退 execution_id 或 unknown", () => {
  assert.equal(executionLabel({ agent_id: "pi", node_key: "review" }), "pi · review");
  assert.equal(executionLabel({ execution_id: "e2" }), "e2");
  assert.equal(executionLabel({}), "unknown");
});
