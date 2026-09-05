# 输出契约：review-script

## 输入

- `knowledge_point.json`：知识点素材（结构见 write-script 的契约）。
- `script.md`：待评审的教学视频脚本。

## 输出

`script_review.json`（UTF-8 JSON 对象）：

```json
{
  "verdict": "pass",
  "dimensions": {
    "teaching_goal": {"score": 8, "comment": "……"},
    "accuracy": {"score": 9, "comment": "……"},
    "pacing": {"score": 7, "comment": "……"}
  },
  "issues": [
    {"section": "例题演示", "problem": "……", "suggestion": "……"}
  ],
  "summary": "一句话总评"
}
```

- `verdict` 只能取 `"pass"` 或 `"revise"`；
- `dimensions` 必须恰好包含 `teaching_goal` / `accuracy` / `pacing`
  三个键，每个含整数 `score`（1-10）与非空 `comment`；
- `issues` 为数组（可以为空），元素含 `section` / `problem` /
  `suggestion` 三个非空字符串；
- `summary` 为非空字符串。

## 校验

运行时优先按下面的机器可读契约段经 harness 内置引擎校验（存在性、
JSON Schema）：

```yaml contract
files:
  - path: script_review.json
    format: json
    schema:
      type: object
      required: [verdict, dimensions, issues, summary]
      properties:
        verdict: {enum: [pass, revise]}
        dimensions:
          type: object
          required: [teaching_goal, accuracy, pacing]
          additionalProperties: false
          properties:
            teaching_goal:
              type: object
              required: [score, comment]
              properties:
                score: {type: integer, minimum: 1, maximum: 10}
                comment: {type: string, minLength: 1}
            accuracy:
              type: object
              required: [score, comment]
              properties:
                score: {type: integer, minimum: 1, maximum: 10}
                comment: {type: string, minLength: 1}
            pacing:
              type: object
              required: [score, comment]
              properties:
                score: {type: integer, minimum: 1, maximum: 10}
                comment: {type: string, minLength: 1}
        issues:
          type: array
          items:
            type: object
            required: [section, problem, suggestion]
            properties:
              section: {type: string, minLength: 1}
              problem: {type: string, minLength: 1}
              suggestion: {type: string, minLength: 1}
        summary: {type: string, minLength: 1}
```

`scripts/validate_output.py` 为 legacy 回落通道
（`python validate_output.py <job_dir>`，退出码 0 为通过），检查项与
契约段一致（空白-only 字符串的严格判定仅 legacy 脚本检查，引擎
`minLength: 1` 只挡空串）。任一不满足则以非零退出码退出并在 stderr
打印原因，节点判失败。校验只管结构，不代 agent 判断评审内容本身是否
合理。
