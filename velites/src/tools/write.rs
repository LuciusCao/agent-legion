//! `write` tool: sandboxed atomic write (tmp file + rename).

use serde_json::Value;

use super::{resolve_in_cwd, ToolContext, ToolError, ToolOutput};

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
    let tmp = resolved.with_extension("velites-tmp");
    std::fs::write(&tmp, content)?;
    std::fs::rename(&tmp, &resolved)?;

    // For write, the meaningful volume is the content written, not the
    // confirmation text.
    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text {
            text: format!("wrote {} bytes to {path}", content.len()),
        }],
        is_error: false,
        output_bytes: content.len() as u64,
    })
}
