//! `read` tool: sandboxed file read with optional 1-based line range.

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
    let lines: Vec<&str> = text.lines().collect();
    let start = (offset - 1).min(lines.len());
    let end = match limit {
        Some(limit) => (start + limit).min(lines.len()),
        None => lines.len(),
    };
    Ok(ToolOutput::text(lines[start..end].join("\n"), false))
}
