//! `write` tool: sandboxed atomic write (tmp file + rename, see
//! `atomic_write` for the temp-file hardening, #922 R-2).

use serde_json::Value;

use super::atomic_write::atomic_write;
use super::{resolve_in_cwd, ToolContext, ToolError, ToolOutput};

pub async fn run(args: &Value, ctx: &ToolContext) -> ToolOutput {
    match run_inner(args, ctx) {
        Ok(output) => output,
        // Argument-shape failures (missing `path`/`content`) reject before
        // any filesystem work and stay timing-free; resolution/write
        // failures happen mid-measurement and keep their totalMs (#469).
        Err(err @ ToolError::InvalidArgs(_)) => ToolOutput::error(err.to_string()),
        Err(err) => ToolOutput::error(err.to_string()).measured(),
    }
}

fn run_inner(args: &Value, ctx: &ToolContext) -> Result<ToolOutput, ToolError> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `path`".into()))?;
    let content = args
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `content`".into()))?;

    let resolved = resolve_in_cwd(&ctx.cwd, path)?;
    if let Some(parent) = resolved.parent() {
        // Safe: `resolved` is already proven to live inside the sandbox.
        std::fs::create_dir_all(parent)?;
    }

    // Atomic write: same-directory tmp file, then rename over the target.
    atomic_write(&resolved, content.as_bytes())?;

    // For write, the meaningful volume is the content written, not the
    // confirmation text. totalMs is filled by the ToolKind::execute dispatch
    // boundary (#469); in-process tools need no timing code of their own.
    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text {
            text: format!("wrote {} bytes to {path}", content.len()),
        }],
        is_error: false,
        output_bytes: content.len() as u64,
        timing: None,
    })
}
