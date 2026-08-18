# 输出契约：generate-questions

## 输入

`knowledge_point.json`：知识点素材（结构见 write-script 的契约）。

## 输出

`exercises.json`（UTF-8 JSON 对象）：

```json
{
  "knowledge_point_id": "fraction-addition-subtraction",
  "exercises": [
    {
      "id": "q1",
      "difficulty": "easy",
      "stem": "计算：3/7 + 2/7 = ?",
      "answer": "5/7",
      "analysis": "同分母分数相加，分母不变，分子相加……"
    }
  ]
}
```

- `knowledge_point_id` 必须与输入知识点的 `id` 一致；
- `exercises` 恰好 5 个元素，`id` 依次为 `q1`..`q5`；
- 每题 `difficulty` ∈ `easy` / `medium` / `hard`，且整体为
  easy ×2、medium ×2、hard ×1；
- 每题 `stem` / `answer` / `analysis` 为非空字符串。

## 校验

`scripts/validate_output.py` 检查：文件存在、JSON 可解析、题量与难度
分布、必填字段、id 序列、知识点 id 一致性（读取 `knowledge_point.json`
比对）。任一不满足则以非零退出码退出并在 stderr 打印原因，节点判失败。
