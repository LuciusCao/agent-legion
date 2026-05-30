请使用 review-subtitles skill 来审核当前视频的字幕。

输入和输出文件都在视频目录下：
- 输入：`subtitles.srt`
- 输出：`subtitles_reviewed.srt` 和 `subtitle_review_report.json`

调用 skill 时传入参数：`input_dir=Video directory`，`output_dir=Video directory`。这里的 `Video directory` 指 prompt 末尾 `Video directory:` 后面的绝对路径。
