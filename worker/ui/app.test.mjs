// worker/ui 无构建无框架，直接用 Node 内置 node:test 跑纯函数测试：
//   node --test worker/ui/app.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import { NUMBER_DEFAULTS, bucketLabel, buildLineChart, executionLabel, fillWindowBuckets, formatElapsed, formatTokens, labelsFromText, latestMetric, linesFromText, metricsParams, modelsFromText, numberField, tokensLastHour } from "./app.js";

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

test("formatTokens 按量级缩写，空值显示 —", () => {
  assert.equal(formatTokens(null), "—");
  assert.equal(formatTokens(950), "950");
  assert.equal(formatTokens(1500), "1.5k");
  assert.equal(formatTokens(12000), "12k");
  assert.equal(formatTokens(2_500_000), "2.5M");
});

test("latestMetric 取最后一个 bucket 的指标，空数据回退 null", () => {
  const payload = { buckets: [{ online_workers: 1 }, { online_workers: 3 }] };
  assert.equal(latestMetric(payload, "online_workers"), 3);
  assert.equal(latestMetric({ buckets: [] }, "online_workers"), null);
  assert.equal(latestMetric(payload, "active_executions"), null);
  assert.equal(latestMetric(null, "online_workers"), null);
});

test("tokensLastHour 只累加最近一小时，全部过旧时回退最后一个 bucket", () => {
  const now = Date.parse("2026-07-26T12:00:00");
  const buckets = [
    { bucket_start: "2026-07-26T10:00:00", total_tokens: 100 },
    { bucket_start: "2026-07-26T11:30:00", total_tokens: 200 },
    { bucket_start: "2026-07-26T11:50:00", total_tokens: 400 },
  ];
  assert.equal(tokensLastHour(buckets, now), 600);
  assert.equal(tokensLastHour(buckets, Date.parse("2026-07-28T12:00:00")), 400);
  assert.equal(tokensLastHour([], now), 0);
});

test("bucketLabel 按粒度格式化（无 Z 后缀按本地时区解析，测试与时区无关）", () => {
  assert.equal(bucketLabel("2026-07-26T05:30:00", "minute"), "05:30");
  assert.equal(bucketLabel("2026-07-26T05:00:00", "hour"), "05:00");
  assert.equal(bucketLabel("2026-07-26T00:00:00", "day"), "07-26");
  assert.equal(bucketLabel("not-a-date", "day"), "not-a-date");
});

test("metricsParams 按粒度带时间窗，固定本机范围 worker_id=self", () => {
  assert.deepEqual(metricsParams("minute"), { granularity: "minute", hours: 6, worker_id: "self" });
  assert.deepEqual(metricsParams("hour"), { granularity: "hour", hours: 24, worker_id: "self" });
  assert.deepEqual(metricsParams("day"), { granularity: "day", days: 7, worker_id: "self" });
});

test("fillWindowBuckets 分钟粒度补齐 360 桶，结束于上一个已完成分钟", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const filled = fillWindowBuckets([], "minute", now);
  assert.equal(filled.length, 360);
  assert.equal(filled[359].bucket_start, "2026-07-27T10:22:00.000Z");
  assert.equal(filled[0].bucket_start, "2026-07-27T04:23:00.000Z");
  assert.equal(filled[0].total_tokens, 0);
  assert.equal(filled[0].active_executions, 0);
});

test("fillWindowBuckets 小时/天粒度结束于当前未完成时段", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const hours = fillWindowBuckets([], "hour", now);
  assert.equal(hours.length, 24);
  assert.equal(hours[23].bucket_start, "2026-07-27T10:00:00.000Z");
  const days = fillWindowBuckets([], "day", now);
  assert.equal(days.length, 7);
  assert.equal(days[6].bucket_start, "2026-07-27T00:00:00.000Z");
});

test("fillWindowBuckets 保留已有桶、稀疏数据不改变窗口范围", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const sparse = [{ bucket_start: "2026-07-27T08:00:00+00:00", total_tokens: 50, active_executions: 1 }];
  const filled = fillWindowBuckets(sparse, "minute", now);
  assert.equal(filled.length, 360);
  const hits = filled.filter((b) => b.total_tokens === 50);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].bucket_start, "2026-07-27T08:00:00+00:00");
});

const CHART_BUCKETS = [
  { bucket_start: "2026-07-26T10:00:00", online_workers: 1, active_executions: 0, total_tokens: 100 },
  { bucket_start: "2026-07-26T11:00:00", online_workers: 3, active_executions: 2, total_tokens: 500 },
  { bucket_start: "2026-07-26T12:00:00", online_workers: 2, active_executions: 1, total_tokens: 300 },
];
const chartPoints = (key) => CHART_BUCKETS.map((b) => ({ label: bucketLabel(b.bucket_start, "hour"), value: b[key] }));

test("buildLineChart 空数据返回空字符串", () => {
  assert.equal(buildLineChart([{ name: "x", color: "#fff", points: [] }]), "");
  assert.equal(buildLineChart([]), "");
});

test("buildLineChart 双折线：生成两条 polyline、图例与 hover 数值带", () => {
  const svg = buildLineChart([
    { name: "在线 Worker", color: "#55e6a6", points: chartPoints("online_workers") },
    { name: "活跃执行", color: "#f0a83c", points: chartPoints("active_executions") },
  ]);
  assert.match(svg, /^<svg /);
  assert.equal(svg.match(/<polyline/g).length, 2);
  assert.ok(svg.includes('stroke="#55e6a6"'));
  assert.ok(svg.includes('stroke="#f0a83c"'));
  assert.ok(svg.includes("在线 Worker"));
  // 每个 bucket 一条 hover 带，title 含两个系列的数值
  assert.equal(svg.match(/hover-zone/g).length, 3);
  assert.ok(svg.includes("在线 Worker: 3"));
  assert.ok(svg.includes("活跃执行: 2"));
  // 折线顶点数量与 bucket 数一致
  assert.equal(svg.match(/\d+\.\d,\d+\.\d/g).length, 6);
});

test("buildLineChart 面积图：额外生成 baseline 闭合的 path", () => {
  const svg = buildLineChart(
    [{ name: "total_tokens", color: "#62e4ad", points: chartPoints("total_tokens"), area: true }],
    { formatValue: formatTokens },
  );
  assert.ok(svg.includes("<path "));
  assert.ok(svg.includes(" Z\""));
  assert.ok(svg.includes("500")); // Y 轴最大值刻度
  assert.ok(svg.includes("total_tokens: 500"));
});

test("buildLineChart 转义文本内容，防止 Host 数据注入 markup", () => {
  const svg = buildLineChart([
    { name: "<b>x</b>", color: "#fff", points: [{ label: "<script>", value: 1 }] },
  ]);
  assert.ok(!svg.includes("<b>x</b>"));
  assert.ok(svg.includes("&lt;b&gt;"));
  assert.ok(svg.includes("&lt;script&gt;"));
});
