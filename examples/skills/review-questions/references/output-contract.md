# 输出契约：review-questions

## 输入

- `knowledge_point.json`：知识点素材（结构见 write-script 的契约）。
- `exercises.json`：待评审的练习题（结构见 generate-questions 的契约）。

## 输出

`exercises_review.json`（UTF-8 JSON 对象）：

```json
{
  "verdict": "pass",
  "exercise_reviews": [
    {"id": "q1", "verdict": "pass", "issues": []},
    {"id": "q3", "verdict": "fail", "issues": ["答案应为 5/6 而非 5/7"]}
  ],
  "summary": "一句话总评"
}
```

- `verdict` 只能取 `"pass"` 或 `"revise"`；
- `exercise_reviews` 必须覆盖 `exercises.json` 中全部题目，每条含
  `id`（与输入一致）、`verdict`（`pass` / `fail`）、`issues`（字符串数组）；
- `summary` 为非空字符串。

## 校验

运行时优先按机器可读契约（本 skill 根目录的 `contract.yaml`）经
harness 内置引擎校验（存在性、JSON Schema）。

引擎不表达的部分由业务规则层 `scripts/validate_output.py` 校验。Agent 收尾
自查显式运行 `AGENT_LEGION_VALIDATE_CONTEXT=agent python validate_output.py <job_dir>`；
Host 复验由平台注入 `host`（退出码 0 为通过）。

两种上下文都检查声明 inputs/outputs 的产物质量：

- `exercise_reviews` 的 id 集合与输入 `exercises.json` 完全一致——
  跨文件业务规则，引擎不表达，仅业务规则脚本检查；
- 空白-only 字符串的严格判定（同上，引擎 `minLength: 1` 只挡空串）。

仅 Agent 上下文检查过程合规：`.work/review-questions.json` 的
`answer_verified_ids` 必须不重复、无遗漏地覆盖全部题目 id，证明答案与解析
核验工序已执行。该文件是非声明工作文件，Host 校验视图结构性不包含它；只有
精确的 `host` 值跳过这一过程检查，变量缺失或 `agent` 都保留完整检查，不能用
文件是否在场推断上下文。

任一不满足则以非零退出码退出并在 stderr 打印原因，节点判失败。
校验只管结构与过程记录，不代 agent 判断评审内容本身是否合理。
