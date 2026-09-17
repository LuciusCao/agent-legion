//! `read` tool: sandboxed file read with optional 1-based line range.
//!
//! Paths may resolve inside the cwd or any extra read-only root (`--skill`
//! dirs, session dir — design §5); escapes are rejected. Output is truncated
//! from the head to 2000 lines or 50KB, whichever is hit first (pi-aligned,
//! design §8); the notice tells the model which `offset` continues the file.

use serde_json::Value;

use super::truncate::{self, TruncatedBy};
use super::{resolve_readable, ToolContext, ToolError, ToolOutput};
use crate::events::ContentBlock;

/// Quote a model-supplied path for shell use in the recovery hints
/// (attack report MEDIUM-5): single quotes with every embedded `'` closed
/// and reopened (`'` → `'\''`), so a filename like `evil; rm -rf ~ .txt`
/// cannot break out of the hint and become a second command when the model
/// copies it verbatim into bash.
fn shell_quoted(path: &str) -> String {
    if path
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "-_./".contains(c))
    {
        return path.to_string();
    }
    format!("'{}'", path.replace('\'', r"'\''"))
}

pub async fn run(args: &Value, ctx: &ToolContext) -> ToolOutput {
    match run_inner(args, ctx) {
        Ok(output) => output,
        // Argument-shape failures (missing `path`) reject before any
        // filesystem work and stay timing-free; resolution/read failures
        // happen mid-measurement and keep their totalMs (#469).
        Err(err @ ToolError::InvalidArgs(_)) => ToolOutput::error(err.to_string()),
        Err(err) => ToolOutput::error(err.to_string()).measured(),
    }
}

fn run_inner(args: &Value, ctx: &ToolContext) -> Result<ToolOutput, ToolError> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `path`".into()))?;
    let offset = args
        .get("offset")
        .and_then(Value::as_u64)
        .map(|n| n.max(1) as usize)
        .unwrap_or(1);
    let limit = args
        .get("limit")
        .and_then(Value::as_u64)
        .map(|n| n as usize);

    let resolved = resolve_readable(&ctx.cwd, &ctx.read_roots, path)?;
    // #637 内存防线：read_to_string 会把整个文件读进内存——行级
    // offset/limit 只是选取，挡不住读入本身（50KB 展示截断发生在读取
    // 之后）。读前 metadata 快照只是普通文件的快速路径（可报出精确
    // 大小）；真正执行上限的是 read_to_string_bounded 的有界读取
    // （#689 review P1：FIFO 的 len() 恒为 0、普通文件检查后仍可增长，
    // 单靠快照可被完全绕过）。超限直接报错并提示用 bash 分段读取。
    let size = std::fs::metadata(&resolved)?.len();
    if size > truncate::MAX_CAPTURE_BYTES {
        let size_text = truncate::format_size(usize::try_from(size).unwrap_or(usize::MAX));
        return Err(ToolError::TooLarge(format!(
            "{path} is {size_text}, over the {} whole-file limit of the read tool. Read it in chunks via bash, e.g. `sed -n '1,2000p' {quoted}`",
            truncate::MAX_CAPTURE_BYTES_DISPLAY,
            quoted = shell_quoted(path),
        )));
    }
    let text = match truncate::read_to_string_bounded(&resolved)? {
        truncate::BoundedRead::Content(text) => text,
        // 读到 cap+1 字节仍未到 EOF：快照撒谎了（FIFO/增长中的文件），
        // 上限由读取本身强制执行。真实总大小不可知，文案不伪造精确值。
        truncate::BoundedRead::Oversized => {
            return Err(ToolError::TooLarge(format!(
                "{path} exceeds the {} whole-file limit of the read tool (growing/FIFO sources report no exact size). Read it in chunks via bash, e.g. `sed -n '1,2000p' {quoted}`",
                truncate::MAX_CAPTURE_BYTES_DISPLAY,
                quoted = shell_quoted(path),
            )));
        }
    };
    // Same counting as truncate::split_lines: a trailing newline does not
    // add an empty line.
    let lines: Vec<&str> = if text.is_empty() {
        Vec::new()
    } else {
        let mut all: Vec<&str> = text.split('\n').collect();
        if text.ends_with('\n') {
            all.pop();
        }
        all
    };
    let total_file_lines = lines.len();
    let start = (offset - 1).min(total_file_lines);
    let end = match limit {
        // Saturating: a huge model-supplied limit must not overflow the add
        // (debug panic / release wrap → slice panic, exit 101 mid-run).
        Some(limit) => start.saturating_add(limit).min(total_file_lines),
        None => total_file_lines,
    };
    let selected = lines[start..end].join("\n");
    let output_bytes = selected.len() as u64;

    let truncation = truncate::truncate_head(&selected);
    let text = if truncation.first_line_exceeds_limit {
        // The first selected line alone exceeds the byte limit; point the
        // model at a bash fallback (pi read.js semantics). `get` guards the
        // empty selection (offset at/past EOF), where no line exists.
        let line_size = truncate::format_size(lines.get(start).map_or(0, |line| line.len()));
        let sed_line = start + 1;
        let display = truncate::MAX_BYTES_DISPLAY;
        format!(
            "[Line {sed_line} is {line_size}, exceeds {display} limit. Use bash: sed -n '{sed_line}p' {quoted} | head -c {}]",
            truncate::DEFAULT_MAX_BYTES,
            quoted = shell_quoted(path),
        )
    } else if truncation.truncated {
        let end_display = start + truncation.output_lines.max(1);
        let next_offset = end_display + 1;
        let limit_note = match truncation.truncated_by {
            Some(TruncatedBy::Bytes) => format!(" ({} limit)", truncate::MAX_BYTES_DISPLAY),
            _ => String::new(),
        };
        let from = start + 1;
        format!(
            "{}\n\n[Showing lines {from}-{end_display} of {total_file_lines}{limit_note}. Use offset={next_offset} to continue.]",
            truncation.content
        )
    } else if limit.is_some() && end < total_file_lines {
        // The user's explicit limit stopped early but the file has more.
        let remaining = total_file_lines - end;
        let next = end + 1;
        format!(
            "{}\n\n[{remaining} more lines in file. Use offset={next} to continue.]",
            truncation.content
        )
    } else {
        truncation.content
    };

    Ok(ToolOutput {
        content: vec![ContentBlock::Text { text }],
        is_error: false,
        output_bytes,
        // totalMs is filled by the ToolKind::execute dispatch boundary (#469);
        // in-process tools need no timing code of their own.
        timing: None,
    })
}
