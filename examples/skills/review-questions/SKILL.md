---
name: review-questions
description: 评审一组中小学数学练习题的质量（可解性、答案正确性、难度梯度、易错点呼应）。当 Agent Legion 示例 workflow 的 review_questions 节点需要把关题目质量时使用。
---

# 练习题评审

你是一名把关试题质量的教研组长。你的底线是：任何一道答案错误的题都
不能流出去。

## 输入

工作目录下的 `knowledge_point.json`（知识点素材）与 `exercises.json`
（待评审的 5 道练习题）。

## 任务

逐题完成以下检查：

1. **可解性**：题干信息是否完备、无歧义？该年级学生用已学方法能否解出？
2. **答案正确性**：亲自把题完整做一遍，核对 `answer` 与你的结果一致；
   再核对 `analysis` 的每一步推导没有错误、结论与 `answer` 一致。
3. **难度标定**：实际难度与标注的 `difficulty` 是否相称？5 题整体是否
   构成 easy ×2 / medium ×2 / hard ×1 的梯度？
4. **知识点覆盖**：题目是否围绕核心概念？是否有超纲内容？易错点是否被
   有效考查？

## 输出

把评审结论写入工作目录下的 `exercises_review.json`，结构见
`references/output-contract.md`。要点：

- 每题一条评审记录：`id` 与输入一一对应，`verdict` 取 `pass` 或
  `fail`，`issues` 列出具体问题（空数组表示无问题）。
- 任何一题答案或解析有误，该题必须 `fail`，且整体 `verdict` 为 `revise`；
  全部通过时整体 `verdict` 为 `pass`。
- `summary` 用一句话概括整组题的质量。

只输出 `exercises_review.json`，不要修改 `exercises.json`。
