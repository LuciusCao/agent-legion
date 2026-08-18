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

`scripts/validate_output.py` 检查：文件存在、JSON 可解析、上述结构与
取值约束满足，且 `exercise_reviews` 的 id 集合与 `exercises.json` 完全
一致。任一不满足则以非零退出码退出并在 stderr 打印原因，节点判失败。
校验只管结构，不代 agent 判断评审内容本身是否合理。
