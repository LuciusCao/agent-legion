//! Tool output truncation, aligned with pi's `truncate.js` semantics.
//!
//! Two independent limits — whichever is hit first wins:
//! - line limit ([`DEFAULT_MAX_LINES`], 2000)
//! - byte limit ([`DEFAULT_MAX_BYTES`], 50KB = 50 * 1024)
//!
//! `truncate_head` keeps the beginning (file reads), `truncate_tail` keeps
//! the end (bash output, where errors and final results live). Neither
//! splits a line, except the tail edge case where the single last line
//! alone exceeds the byte limit: then the tail of that line is kept.
//!
//! [`MAX_CAPTURE_BYTES`] is a different kind of limit — not display
//! truncation but the read-side in-memory cap shared by the bash output
//! capture and the `read`/`json` whole-file loads (see its doc).

pub const DEFAULT_MAX_LINES: usize = 2000;
pub const DEFAULT_MAX_BYTES: usize = 50 * 1024;
/// Human-readable form of [`DEFAULT_MAX_BYTES`] used in notices.
pub const MAX_BYTES_DISPLAY: &str = "50KB";

/// 读入内存的字节上限：bash 输出捕获（每条 pipe）与 `read`/`json` 整文件
/// 读入共用。
///
/// 背景（#637）：曾发生单个 velites 进程在数秒内无界分配出远超物理内存
/// 的堆、导致机器被系统内存清场（jetsam/OOM）的事故——根因是无界读取
/// （bash 子进程输出全量累积进内存），展示层的 50KB 截断发生在读取
/// 之后，挡不住读取本身。4 MiB 远大于展示上限，且与 ACP terminal 侧的
/// 输出字节上限（`DEFAULT_OUTPUT_BYTE_LIMIT`，4 MiB）同值。bash 侧触顶
/// 后保留头部、其余字节只计数不保留；文件侧超限在读前直接报错。
pub const MAX_CAPTURE_BYTES: u64 = 4 * 1024 * 1024;
/// Human-readable form of [`MAX_CAPTURE_BYTES`] used in notices.
pub const MAX_CAPTURE_BYTES_DISPLAY: &str = "4MB";

/// One #637 bounded whole-file read outcome: the content (at most
/// [`MAX_CAPTURE_BYTES`] bytes, read to EOF), or the observation that the
/// source produced more than the cap.
#[derive(Debug)]
pub(crate) enum BoundedRead {
    Content(String),
    Oversized,
}

/// #637 读侧硬上限：从已打开的句柄最多读入 `MAX_CAPTURE_BYTES + 1` 字节
/// （`read`/`json` 的整文件加载共用）。
///
/// 真正执行上限的是这次读取本身，而不是读前的 `metadata().len()` 快照
/// ——FIFO 的 `len()` 恒为 0、普通文件在检查后仍可被继续写入，快照都可
/// 被绕过（调用方保留快照仅作为普通文件的快速路径，可给出精确大小）。
/// 多读的 1 字节用于区分「恰好等于上限」（合法）与「超过上限」（报
/// [`BoundedRead::Oversized`]），内存峰值因此是 cap+1 而非两倍 cap。
/// UTF-8 校验失败复用 `read_to_string` 的错误消息（InvalidData）。
pub(crate) fn read_to_string_bounded(path: &std::path::Path) -> std::io::Result<BoundedRead> {
    use std::io::Read;
    let file = std::fs::File::open(path)?;
    let cap = usize::try_from(MAX_CAPTURE_BYTES).unwrap_or(usize::MAX);
    let mut raw = Vec::new();
    file.take(MAX_CAPTURE_BYTES + 1).read_to_end(&mut raw)?;
    if raw.len() > cap {
        return Ok(BoundedRead::Oversized);
    }
    match String::from_utf8(raw) {
        Ok(text) => Ok(BoundedRead::Content(text)),
        Err(_) => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "stream did not contain valid UTF-8",
        )),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TruncatedBy {
    Lines,
    Bytes,
}

#[derive(Debug)]
pub struct Truncation {
    pub content: String,
    pub truncated: bool,
    pub truncated_by: Option<TruncatedBy>,
    pub total_lines: usize,
    pub total_bytes: usize,
    pub output_lines: usize,
    pub output_bytes: usize,
    /// Tail truncation only: the single retained line is a partial tail of
    /// the original last line (that line alone exceeds the byte limit).
    pub last_line_partial: bool,
    /// Head truncation only: the first line alone exceeds the byte limit,
    /// so nothing can be shown.
    pub first_line_exceeds_limit: bool,
}

impl Truncation {
    fn untruncated(content: &str, total_lines: usize) -> Self {
        Self {
            content: content.to_string(),
            truncated: false,
            truncated_by: None,
            total_lines,
            total_bytes: content.len(),
            output_lines: total_lines,
            output_bytes: content.len(),
            last_line_partial: false,
            first_line_exceeds_limit: false,
        }
    }
}

/// Format bytes as a human-readable size (pi `formatSize`).
pub fn format_size(bytes: usize) -> String {
    if bytes < 1024 {
        format!("{bytes}B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1}KB", bytes as f64 / 1024.0)
    } else {
        format!("{:.1}MB", bytes as f64 / (1024.0 * 1024.0))
    }
}

/// Split like pi's `splitLinesForCounting`: on `\n`, with the empty tail
/// after a trailing newline dropped; empty content has zero lines.
fn split_lines(content: &str) -> Vec<&str> {
    if content.is_empty() {
        return Vec::new();
    }
    let mut lines: Vec<&str> = content.split('\n').collect();
    if content.ends_with('\n') {
        lines.pop();
    }
    lines
}

/// Truncate from the head, keeping the first lines (file-read semantics).
pub fn truncate_head(content: &str) -> Truncation {
    truncate_head_within(content, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES)
}

/// [`truncate_head`] with an explicit budget — the bash capture-capped branch
/// splits the display budget between the two stream heads (#779 train R4
/// review P2: stdout's kept head must not squeeze the fully captured stderr
/// out of the display).
pub fn truncate_head_within(content: &str, max_lines: usize, max_bytes: usize) -> Truncation {
    let total_bytes = content.len();
    let lines = split_lines(content);
    let total_lines = lines.len();
    if total_lines <= max_lines && total_bytes <= max_bytes {
        return Truncation::untruncated(content, total_lines);
    }
    if max_lines == 0 || lines[0].len() > max_bytes {
        return Truncation {
            content: String::new(),
            truncated: true,
            truncated_by: Some(TruncatedBy::Bytes),
            total_lines,
            total_bytes,
            output_lines: 0,
            output_bytes: 0,
            last_line_partial: false,
            first_line_exceeds_limit: true,
        };
    }
    let mut kept: Vec<&str> = Vec::new();
    let mut kept_bytes = 0usize;
    let mut truncated_by = TruncatedBy::Lines;
    for (index, line) in lines.iter().enumerate().take(max_lines) {
        // +1 for the newline separator between kept lines (same as pi).
        let line_bytes = line.len() + usize::from(index > 0);
        if kept_bytes + line_bytes > max_bytes {
            truncated_by = TruncatedBy::Bytes;
            break;
        }
        kept.push(line);
        kept_bytes += line_bytes;
    }
    if kept.len() >= max_lines && kept_bytes <= max_bytes {
        truncated_by = TruncatedBy::Lines;
    }
    let content = kept.join("\n");
    Truncation {
        output_lines: kept.len(),
        output_bytes: content.len(),
        content,
        truncated: true,
        truncated_by: Some(truncated_by),
        total_lines,
        total_bytes,
        last_line_partial: false,
        first_line_exceeds_limit: false,
    }
}

/// Truncate from the tail, keeping the last lines (bash-output semantics:
/// errors and results live at the end).
pub fn truncate_tail(content: &str) -> Truncation {
    truncate_tail_within(content, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES)
}

/// [`truncate_tail`] with an explicit budget — like [`truncate_head_within`],
/// the bash capture-capped branch gives each stream head/tail its own share
/// of the display budget (#779 train R4 review P2: stderr that was captured
/// in FULL (no hit_cap) but exceeds its reserved share keeps its TAIL — the
/// final diagnostic lives there, matching the regular bash truncation).
pub fn truncate_tail_within(content: &str, max_lines: usize, max_bytes: usize) -> Truncation {
    let total_bytes = content.len();
    let lines = split_lines(content);
    let total_lines = lines.len();
    if total_lines <= max_lines && total_bytes <= max_bytes {
        return Truncation::untruncated(content, total_lines);
    }
    let mut kept: Vec<&str> = Vec::new(); // built back-to-front
    let mut partial: Option<String> = None;
    let mut kept_bytes = 0usize;
    let mut truncated_by = TruncatedBy::Lines;
    let mut last_line_partial = false;
    for line in lines.iter().rev().take(max_lines) {
        // +1 for the newline separator; the last line of the output has none.
        let line_bytes = line.len() + usize::from(!kept.is_empty());
        if kept_bytes + line_bytes > max_bytes {
            truncated_by = TruncatedBy::Bytes;
            if kept.is_empty() {
                // Edge case: the last line alone exceeds the byte limit —
                // keep the tail of the line (pi's only partial-line case).
                let tail = tail_within_bytes(line, max_bytes);
                kept_bytes = tail.len();
                partial = Some(tail.to_string());
                last_line_partial = true;
            }
            break;
        }
        kept.push(line);
        kept_bytes += line_bytes;
    }
    if kept.len() >= max_lines && kept_bytes <= max_bytes {
        truncated_by = TruncatedBy::Lines;
    }
    kept.reverse();
    let content = match partial {
        Some(tail) => tail,
        None => kept.join("\n"),
    };
    let output_lines = kept.len() + usize::from(last_line_partial);
    Truncation {
        output_bytes: content.len(),
        output_lines,
        content,
        truncated: true,
        truncated_by: Some(truncated_by),
        total_lines,
        total_bytes,
        last_line_partial,
        first_line_exceeds_limit: false,
    }
}

/// Keep the last `max_bytes` of `line`, not splitting a multi-byte UTF-8
/// character (pi `truncateStringToBytesFromEnd`).
fn tail_within_bytes(line: &str, max_bytes: usize) -> &str {
    if line.len() <= max_bytes {
        return line;
    }
    let mut start = line.len() - max_bytes;
    while !line.is_char_boundary(start) {
        start += 1;
    }
    &line[start..]
}

/// Format bash output after either stream hit its capture cap. A capped
/// stream keeps its head; a fully captured stream keeps its tail when its
/// display share is too small. No complete output file can exist in this
/// branch, so the notice always points at redirected, chunked bash reads.
pub(crate) fn format_bash_capture_capped(
    stdout_text: &str,
    stdout_hit_cap: bool,
    stderr_text: &str,
    stderr_hit_cap: bool,
    output_bytes: u64,
) -> String {
    // #637 触顶分支：保留的是流头部、尾部已被丢弃——完整输出在内存
    // 中已不存在，绝不能走 write_full_output（那会假装有完整输出可
    // 指认）。展示层保留头部的前 2000 行 / 50KB，通知说清「尾部已
    // 丢弃、无完整输出文件」，并指路：重定向到文件再用 bash 流式
    // 分段命令读取。与 tail 截断方向相反（那边错误/结果在末尾、保
    // 尾；触顶后尾部已丢，只能保头）。
    //
    // #689 review P2：恢复提示必须与 read 工具的自洽——read 对超过
    // 4 MiB 的整文件在读前直接报错（上限检查先于 offset/limit 行
    // 选取），「用 read 的 offset/limit 分段读」这个流程走不通，模型
    // 会陷入「重定向 → read 报错 → 重定向」死循环。bash 分段命令
    // （sed 按行窗、tail+head 翻页）与 read 超限时自己的提示同构，
    // 是唯一可走通的出口。
    //
    // #779 列车 R4 复审 P2：展示预算按流分配——stderr 先预留总预算的
    // 一半（其实际所需为限），stdout 头部用剩余。合并文本再整体保头
    // 会让 stdout 的 4 MiB 头部把完整采集到的 stderr 全部挤出展示面，
    // 失败命令的错误原因随之丢失。两流各自的截断方向对称（R4 P2 跟进
    // 及其镜像）：未触顶（完整采集）一侧保尾——最终诊断/结果在末尾，
    // 与常规截断同语义；触顶一侧保头——尾部已在采集侧丢弃。
    // stderr 在预留份额内不可展示（首行即超限，如无换行的超长行）时
    // 仅保留分节标记——与修复前合并文本截断后的形状一致。
    let per_stream = |text: &str, hit_cap: bool, lines: usize, bytes: usize| {
        if hit_cap {
            truncate_head_within(text, lines, bytes)
        } else {
            truncate_tail_within(text, lines, bytes)
        }
    };
    let stderr_budget = (DEFAULT_MAX_LINES / 2, DEFAULT_MAX_BYTES / 2);
    let stderr_truncation = per_stream(
        stderr_text,
        stderr_hit_cap,
        stderr_budget.0,
        stderr_budget.1,
    );
    let stdout_budget_bytes = DEFAULT_MAX_BYTES - stderr_truncation.output_bytes;
    let stdout_truncation = per_stream(
        stdout_text,
        stdout_hit_cap,
        DEFAULT_MAX_LINES - stderr_truncation.output_lines,
        stdout_budget_bytes,
    );
    // #779 列车 R4 复审 P2 跟进：提示按各流实际截断方向分别说明——统一
    // 声称「head above is kept」会在未触顶流保尾展示时与内容矛盾（模型
    // 会误判所见日志的位置）。触顶流：保头（尾部采集侧已丢）；完整采集
    // 但超份额的流：保尾（头部按份额剪掉）；完整且未超份额：完整展示。
    // 再跟进：提示依据该流实际保留的正文生成——触顶流首行即超份额时
    // truncate_head_within 返回空内容，[stderr] 分节标记仍会让 kept
    // 非空（绕过 notice-only 分支），不得声称 head 已保留——明示什么
    // 都没展示 + 实际预算数值（与首行超限 notice-only 分支同语义）；
    // 数值取该流的真实预算（stdout = 总预算 − stderr 实际占用，stderr
    // = 预留份额），不是固定预留份额（#779 列车 R4 P2 再跟进）。
    let stream_note = |name: &str, hit_cap: bool, tr: &Truncation, budget: usize| {
        if hit_cap && tr.first_line_exceeds_limit {
            format!(
                "{name} hit the cap, and its first line alone exceeds the {} display budget, so nothing of it is shown; the tail was dropped at capture",
                format_size(budget),
            )
        } else if hit_cap {
            format!("{name} hit the cap: the head is kept, the tail was dropped")
        } else if tr.truncated {
            format!(
                "{name} was fully captured; shown tail-first (its head is trimmed to the display share)"
            )
        } else {
            format!("{name} was fully captured")
        }
    };
    let notice = format!(
        "[Output capture stopped after {} at the {} per-stream cap ({}; {}). No full-output file was saved — the dropped tail no longer exists. Rerun with output redirected to a file (e.g. `cmd > out.log 2>&1`) and read it in chunks with bash, e.g. `sed -n '1,2000p' out.log`, `tail -n +2001 out.log | head -n 2000` (the read tool rejects whole files over {} even with offset/limit).]",
        format_size(usize::try_from(output_bytes).unwrap_or(usize::MAX)),
        MAX_CAPTURE_BYTES_DISPLAY,
        stream_note(
            "stdout",
            stdout_hit_cap,
            &stdout_truncation,
            stdout_budget_bytes,
        ),
        stream_note(
            "stderr",
            stderr_hit_cap,
            &stderr_truncation,
            stderr_budget.1,
        ),
        MAX_CAPTURE_BYTES_DISPLAY,
    );
    let mut kept = stdout_truncation.content;
    if !stderr_text.is_empty() {
        // 采集到 stderr 就保留 [stderr] 分节标记（份额内不可展示时
        // 内容为空，与修复前合并文本截断后的形状一致）。
        if !kept.is_empty() {
            kept.push('\n');
        }
        kept.push_str("[stderr]\n");
        kept.push_str(&stderr_truncation.content);
    }
    if kept.is_empty() {
        // 首行就超过展示上限（如单个超长行）：无内容可展示，通知
        // 独立成文，不加前导空行——且不说「head above is kept」，
        // 上面没有任何内容。
        format!(
            "[Output capture stopped after {} at the {} per-stream cap (the first line alone exceeds the {} display limit, so no content is shown; the tail was dropped). No full-output file was saved — the dropped tail no longer exists. Rerun with output redirected to a file (e.g. `cmd > out.log 2>&1`) and read it in chunks with bash, e.g. `sed -n '1,2000p' out.log` (the read tool rejects whole files over {} even with offset/limit).]",
            format_size(usize::try_from(output_bytes).unwrap_or(usize::MAX)),
            MAX_CAPTURE_BYTES_DISPLAY,
            MAX_BYTES_DISPLAY,
            MAX_CAPTURE_BYTES_DISPLAY,
        )
    } else {
        kept.push_str("\n\n");
        kept.push_str(&notice);
        kept
    }
}
