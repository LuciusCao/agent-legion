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

运行时优先按下面的机器可读契约段经 harness 内置引擎校验（存在性、
JSON Schema）：

```yaml contract
files:
  - path: exercises.json
    format: json
    schema:
      type: object
      required: [exercises]
      properties:
        knowledge_point_id:
          type: string
        exercises:
          type: array
          minItems: 5
          maxItems: 5
          allOf:
            # 位置约束（prefixItems 所在分支不能再带 items，否则 items 只约束
            # 第 6 个起的元素）：id 依次为 q1..q5。
            - prefixItems:
                - properties: {id: {enum: ["q1"]}}
                - properties: {id: {enum: ["q2"]}}
                - properties: {id: {enum: ["q3"]}}
                - properties: {id: {enum: ["q4"]}}
                - properties: {id: {enum: ["q5"]}}
            # 通用元素约束：作用于全部元素。
            - items:
                type: object
                required: [id, difficulty, stem, answer, analysis]
                properties:
                  id: {type: string, minLength: 1}
                  difficulty: {enum: [easy, medium, hard]}
                  stem: {type: string, minLength: 1}
                  answer: {type: string, minLength: 1}
                  analysis: {type: string, minLength: 1}
            # 难度分布：easy ×2、medium ×2、hard ×1。
            - contains: {properties: {difficulty: {enum: [easy]}}}
              minContains: 2
              maxContains: 2
            - contains: {properties: {difficulty: {enum: [medium]}}}
              minContains: 2
              maxContains: 2
            - contains: {properties: {difficulty: {enum: [hard]}}}
              minContains: 1
              maxContains: 1
```

引擎不表达的部分由 `scripts/validate_output.py` legacy 脚本兜底
（`python validate_output.py <job_dir>`，退出码 0 为通过）：

- `knowledge_point_id` 与输入 `knowledge_point.json` 的 id 一致性——
  跨文件业务规则，引擎不表达，仅 legacy 脚本检查；
- 空白-only 字符串的严格判定（引擎 `minLength: 1` 只挡空串，legacy
  脚本按 `strip()` 后非空判定）。

任一不满足则以非零退出码退出并在 stderr 打印原因，节点判失败。
