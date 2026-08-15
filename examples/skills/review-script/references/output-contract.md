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

`scripts/validate_output.py` 检查：文件存在、JSON 可解析、上述结构与
取值约束全部满足。任一不满足则以非零退出码退出并在 stderr 打印原因，
节点判失败。校验只管结构，不代 agent 判断评审内容本身是否合理。
