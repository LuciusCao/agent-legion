请使用 generate-interactions skill 来设计当前视频的互动节点。

输入和输出文件都在视频目录下：
- 输入：`subtitles_reviewed.srt`
- 输出：`interactions.json`

调用 skill 时传入参数：`input_dir=Video directory`，`output_dir=Video directory`。这里的 `Video directory` 指 prompt 末尾 `Video directory:` 后面的绝对路径。

注意：本 pipeline 在 assemble 阶段之前通常还没有 `metadata.json`；如果它不存在，不要依赖它，也不要为此中断，直接根据字幕内容生成互动节点。
