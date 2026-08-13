// worker/ui 无构建无框架，直接用 Node 内置 node:test 跑纯函数测试：
//   node --test worker/ui/app.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import { NUMBER_DEFAULTS, bucketLabel, chartSeriesData, executionLabel, fillWindowBuckets, foldLogLines, formatElapsed, formatTokens, groupExecutions, hasChartData, labelsFromText, latestMetric, linesFromText, metricsParams, modelsFromText, numberField, phaseProgress, runOccupancy, tokensLastHour } from "./app.js";

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
  // 批次 2：code 执行池默认 0（仅 agent），与 config_store._DEFAULTS 对齐。
  assert.equal(numberField(formDataLike({ max_code_concurrency: "" }), "max_code_concurrency"), 0);
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

test("groupExecutions 按 node_key 分组并按数量排序", () => {
  assert.deepEqual(groupExecutions([
    { execution_id: "e1", node_key: "review", phase: "running" },
    { execution_id: "e2", node_key: "upload", phase: "uploading" },
    { execution_id: "e3", node_key: "review", phase: "claimed" },
  ]).map((group) => [group.name, group.executions.length, group.phase]), [
    ["review", 2, "running"],
    ["upload", 1, "uploading"],
  ]);
});

test("phaseProgress 按执行阶段提供稳定的阶段进度", () => {
  assert.equal(phaseProgress("claimed"), 16);
  assert.equal(phaseProgress("running"), 68);
  assert.equal(phaseProgress("uploading"), 88);
  assert.equal(phaseProgress("unknown"), 12);
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
  assert.equal(bucketLabel("2026-07-26T05:30:00", "6h"), "05:30");
  assert.equal(bucketLabel("2026-07-26T05:00:00", "24h"), "05:00");
  assert.equal(bucketLabel("2026-07-26T08:00:00", "30d"), "07-26 08:00");
  assert.equal(bucketLabel("not-a-date", "30d"), "not-a-date");
});

test("metricsParams 固定本机范围 worker_id=self，窗口由粒度唯一决定", () => {
  assert.deepEqual(metricsParams("6h"), { granularity: "6h", worker_id: "self" });
  assert.deepEqual(metricsParams("24h"), { granularity: "24h", worker_id: "self" });
  assert.deepEqual(metricsParams("30d"), { granularity: "30d", worker_id: "self" });
});

test("fillWindowBuckets 6h 补齐 360 个 1 分钟桶，结束于上一个已完成分钟", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const filled = fillWindowBuckets([], "6h", now);
  assert.equal(filled.length, 360);
  assert.equal(filled[359].bucket_start, "2026-07-27T10:22:00.000Z");
  assert.equal(filled[0].bucket_start, "2026-07-27T04:23:00.000Z");
  // 完全无真实数据时视为「数据未出」，不填 0
  assert.equal(filled[0].total_tokens, null);
  assert.equal(filled[0].active_executions, null);
});

test("fillWindowBuckets 24h/30d 结束于当前进行中的桶", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const hours = fillWindowBuckets([], "24h", now);
  assert.equal(hours.length, 288);
  assert.equal(hours[287].bucket_start, "2026-07-27T10:20:00.000Z");
  assert.equal(hours[0].bucket_start, "2026-07-26T10:25:00.000Z");
  const days = fillWindowBuckets([], "30d", now);
  assert.equal(days.length, 180);
  assert.equal(days[179].bucket_start, "2026-07-27T08:00:00.000Z");
  assert.equal(days[0].bucket_start, "2026-06-27T12:00:00.000Z");
});

test("fillWindowBuckets 保留已有桶、稀疏数据不改变窗口范围", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const sparse = [{ bucket_start: "2026-07-27T08:00:00+00:00", total_tokens: 50, active_executions: 1 }];
  const filled = fillWindowBuckets(sparse, "6h", now);
  assert.equal(filled.length, 360);
  const hits = filled.filter((b) => b.total_tokens === 50);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].bucket_start, "2026-07-27T08:00:00+00:00");
});

test("fillWindowBuckets 最后真实数据点之后填 null、之前缺失桶填 0", () => {
  const now = Date.parse("2026-07-27T10:23:45Z");
  const sparse = [{ bucket_start: "2026-07-27T08:00:00+00:00", total_tokens: 50, active_executions: 1 }];
  const filled = fillWindowBuckets(sparse, "6h", now);
  // 08:00 之前的缺失桶：真实无数据，填 0
  assert.equal(filled[0].total_tokens, 0);
  assert.equal(filled[0].active_executions, 0);
  // 08:00 之后（Host 尚未聚合写入）的尾部桶：数据未出，填 null
  const last = filled[filled.length - 1];
  assert.equal(last.bucket_start, "2026-07-27T10:22:00.000Z");
  assert.equal(last.total_tokens, null);
  assert.equal(last.active_executions, null);
});

test("chartSeriesData 生成 uPlot 数据：x 为 unix 秒，null 保留为缺口", () => {
  const buckets = [
    { bucket_start: "2026-07-26T10:00:00Z", active_executions: 3, input_tokens: 100 },
    { bucket_start: "2026-07-26T10:05:00Z", active_executions: null, input_tokens: null },
    { bucket_start: "2026-07-26T10:10:00Z", active_executions: 5, input_tokens: 300 },
  ];
  const [x, active, input] = chartSeriesData(buckets, ["active_executions", "input_tokens"]);
  assert.deepEqual(x, [
    Date.parse("2026-07-26T10:00:00Z") / 1000,
    Date.parse("2026-07-26T10:05:00Z") / 1000,
    Date.parse("2026-07-26T10:10:00Z") / 1000,
  ]);
  assert.deepEqual(active, [3, null, 5]);
  assert.deepEqual(input, [100, null, 300]);
});

test("hasChartData 任一 key 有非空值即为 true，全 null/空数组为 false", () => {
  assert.equal(hasChartData([{ active_executions: null }, { active_executions: 2 }], ["active_executions"]), true);
  assert.equal(hasChartData([{ active_executions: null, input_tokens: null }], ["active_executions", "input_tokens"]), false);
  assert.equal(hasChartData([], ["active_executions"]), false);
});

test("runOccupancy 只统计占用运行槽位的阶段", () => {
  const executions = [
    { phase: "claimed" },
    { phase: "downloading" },
    { phase: "running" },
    { phase: "queued_upload" },
    { phase: "uploading" },
  ];
  assert.equal(runOccupancy(executions), 3);
  assert.equal(runOccupancy([]), 0);
  assert.equal(runOccupancy(undefined), 0);
});

test("foldLogLines 忽略时间戳折叠连续重复行，展示最新一行并标注重复次数", () => {
  const lines = [
    "[15:55:08] worker slots 0/96, upload queue depth 0",
    "[15:55:25] worker slots 0/96, upload queue depth 0",
    "[15:55:41] worker slots 0/96, upload queue depth 0",
    "[15:55:57] 警告：挂载的配置文件与本地状态副本不一致",
    "[15:56:13] worker slots 0/96, upload queue depth 0",
  ];
  assert.deepEqual(foldLogLines(lines), [
    "[15:55:41] worker slots 0/96, upload queue depth 0  × 3",
    "[15:55:57] 警告：挂载的配置文件与本地状态副本不一致",
    "[15:56:13] worker slots 0/96, upload queue depth 0",
  ]);
  assert.deepEqual(foldLogLines([]), []);
  assert.deepEqual(foldLogLines(undefined), []);
});
