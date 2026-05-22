请使用 review-interactions skill 来审核当前视频的内容。

输入和输出文件都在视频目录下：
- 输入：`chapters.json`、`interactions.json`、`subtitles_reviewed.srt`
- 输出：`checklist.json` 和 `review_result.json`

调用 skill 时传入参数：`input_dir=Video directory`，`output_dir=Video directory`。这里的 `Video directory` 指 prompt 末尾 `Video directory:` 后面的绝对路径。

注意：写完 `checklist.json` 后，运行 `review_content.py` 生成 `review_result.json`。
