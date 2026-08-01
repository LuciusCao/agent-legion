//! `read` tool: sandboxed file read with optional 1-based line range.
//!
//! Output is truncated from the head to 2000 lines or 50KB, whichever is
//! hit first (pi-aligned, design §8); the notice tells the model which
//! `offset` continues the file.

use serde_json::Value;

use super::truncate::{self, TruncatedBy};
use super::{resolve_in_cwd, ToolContext, ToolError, ToolOutput};
use crate::events::ContentBlock;

pub async fn run(args: &Value, ctx: &ToolContext) -> ToolOutput {
    match run_inner(args, ctx) {
        Ok(output) => output,
        Err(err) => ToolOutput::error(err.to_string()),
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

    let resolved = resolve_in_cwd(&ctx.cwd, path)?;
    let text = std::fs::read_to_string(&resolved)?;
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
        Some(limit) => (start + limit).min(total_file_lines),
        None => total_file_lines,
    };
    let selected = lines[start..end].join("\n");
    let output_bytes = selected.len() as u64;

    let truncation = truncate::truncate_head(&selected);
    let text = if truncation.first_line_exceeds_limit {
        // The first selected line alone exceeds the byte limit; point the
        // model at a bash fallback (pi read.js semantics).
        let line_size = truncate::format_size(lines[start].len());
        format!(
            "[Line {} is {}, exceeds {} limit. Use bash: sed -n '{}p' {} | head -c {}]",
            start + 1,
            line_size,
            truncate::MAX_BYTES_DISPLAY,
            start + 1,
            path,
            truncate::DEFAULT_MAX_BYTES,
        )
    } else if truncation.truncated {
        let end_display = start + truncation.output_lines.max(1);
        let next_offset = end_display + 1;
        let limit_note = match truncation.truncated_by {
            Some(TruncatedBy::Bytes) => format!(" ({} limit)", truncate::MAX_BYTES_DISPLAY),
            _ => String::new(),
        };
        format!(
            "{}\n\n[Showing lines {}-{} of {}{}. Use offset={} to continue.]",
            truncation.content,
            start + 1,
            end_display,
            total_file_lines,
            limit_note,
            next_offset,
        )
    } else if limit.is_some() && end < total_file_lines {
        // The user's explicit limit stopped early but the file has more.
        let remaining = total_file_lines - end;
        format!(
            "{}\n\n[{} more lines in file. Use offset={} to continue.]",
            truncation.content,
            remaining,
            end + 1,
        )
    } else {
        truncation.content
    };

    Ok(ToolOutput {
        content: vec![ContentBlock::Text { text }],
        is_error: false,
        output_bytes,
    })
}
