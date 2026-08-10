//! `write` tool: sandboxed atomic write (tmp file + rename).

use std::path::{Path, PathBuf};

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
    let tmp = tmp_path(&resolved);
    if let Err(err) = std::fs::write(&tmp, content).and_then(|_| std::fs::rename(&tmp, &resolved)) {
        // Leave no half-written tmp behind on failure.
        let _ = std::fs::remove_file(&tmp);
        return Err(err.into());
    }

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

/// The tmp path for one atomic write: the FULL file name plus a suffix
/// (`a.md` → `a.md.velites-tmp`). `with_extension` would map `a.md` and
/// `a.txt` onto the same `a.velites-tmp`, letting two writes to same-stem
/// files clobber each other's tmp.
fn tmp_path(target: &Path) -> PathBuf {
    let file_name = target
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("velites-tmp");
    target.with_file_name(format!("{file_name}.velites-tmp"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tmp_path_carries_the_full_file_name() {
        assert_eq!(
            tmp_path(Path::new("/d/a.md")),
            PathBuf::from("/d/a.md.velites-tmp")
        );
        // Same stem, different extensions: no shared tmp name.
        assert_eq!(
            tmp_path(Path::new("/d/a.txt")),
            PathBuf::from("/d/a.txt.velites-tmp")
        );
        assert_eq!(
            tmp_path(Path::new("/d/no-ext")),
            PathBuf::from("/d/no-ext.velites-tmp")
        );
    }
}
