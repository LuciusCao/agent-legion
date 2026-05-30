请使用 slice-chapters skill 来切分当前视频的章节。

输入和输出文件都在视频目录下：
- 输入：`subtitles_reviewed.srt`
- 输出：`chapters_raw.json` 和 `chapters.json`

调用 skill 时传入参数：`input_dir=Video directory`，`output_dir=Video directory`。这里的 `Video directory` 指 prompt 末尾 `Video directory:` 后面的绝对路径。

注意：先生成 `chapters_raw.json`，然后运行 snap 工具生成 `chapters.json`。
