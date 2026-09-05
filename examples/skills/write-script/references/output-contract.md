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

运行时优先按下面的机器可读契约段经 harness 内置引擎校验（存在性、文本
长度、必备标题）：

```yaml contract
files:
  - path: script.md
    format: text
    min_chars: 200
    required_headings: ["## 开场导入", "## 概念讲解", "## 例题演示", "## 易错点提醒", "## 小结"]
```

`scripts/validate_output.py` 为 legacy 回落通道
（`python validate_output.py <job_dir>`，退出码 0 为通过），检查项与
契约段一致：

- `script.md` 存在且为合法 UTF-8 文本；
- 五个必备小节标题全部出现；
- 去空白后正文长度不少于 200 字符。

计数口径差异：引擎 `min_chars` 按去首尾空白后的字符数计，legacy 脚本
按去除全部空白后的字符数计（更严格）。校验失败以非零退出码退出并在
stderr 打印缺失项，节点判失败。
