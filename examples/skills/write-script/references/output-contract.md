# 输出契约：write-script

## 输入

`knowledge_point.json`（由 intake 节点生成）：

```json
{
  "knowledge_point": {
    "id": "fraction-addition-subtraction",
    "title": "分数加减法",
    "grade": "小学五年级",
    "subject": "小学数学",
    "summary": "核心概念的若干段落……",
    "common_mistakes": ["易错点 1", "易错点 2"]
  },
  "source": {"file": "fraction-addition-subtraction.md"}
}
```

## 输出

`script.md`（UTF-8 文本）：教学视频口播脚本，必须包含
`## 开场导入` `## 概念讲解` `## 例题演示` `## 易错点提醒` `## 小结`
五个二级标题，正文总长度不少于 200 字。

## 校验

`scripts/validate_output.py` 由运行时在 agent 结束后执行
（`python validate_output.py <job_dir>`，退出码 0 为通过）：

- `script.md` 存在且为合法 UTF-8 文本；
- 五个必备小节标题全部出现；
- 去空白后正文长度不少于 200 字符。

校验失败时脚本以非零退出码退出并在 stderr 打印缺失项，节点判失败。
