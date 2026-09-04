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

运行时优先按下面的机器可读契约段经 harness 内置引擎校验（存在性、
JSON Schema）：

```yaml contract
files:
  - path: exercises_review.json
    format: json
    schema:
      type: object
      required: [verdict, exercise_reviews, summary]
      properties:
        verdict: {enum: [pass, revise]}
        summary: {type: string, minLength: 1}
        exercise_reviews:
          type: array
          items:
            type: object
            required: [id, verdict, issues]
            properties:
              id: {type: string, minLength: 1}
              verdict: {enum: [pass, fail]}
              issues:
                type: array
                items: {type: string}
```

引擎不表达的部分由 `scripts/validate_output.py` legacy 脚本兜底
（`python validate_output.py <job_dir>`，退出码 0 为通过）：

- `exercise_reviews` 的 id 集合与输入 `exercises.json` 完全一致——
  跨文件业务规则，引擎不表达，仅 legacy 脚本检查；
- 空白-only 字符串的严格判定（同上，引擎 `minLength: 1` 只挡空串）。

任一不满足则以非零退出码退出并在 stderr 打印原因，节点判失败。
校验只管结构，不代 agent 判断评审内容本身是否合理。
