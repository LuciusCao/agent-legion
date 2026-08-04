//! Skill loading: read the SKILL.md of each explicitly passed `--skill`
//! directory. This reads exactly one file per flag — there is deliberately
//! no directory scanning or discovery of any kind (zero-auto-discovery
//! invariant, design §5).
//!
//! Two Pi behaviors are mirrored here:
//!
//! - The SKILL.md YAML frontmatter (`---` ... `---`, name/description
//!   metadata) is STRIPPED before injection into the system prompt; only the
//!   markdown body is context.
//! - Sibling files under the skill directory (references, scripts) are NOT
//!   loaded; the model reads them on demand with the `read` tool.

use std::path::Path;

use anyhow::Context;

pub fn load_skill(dir: &Path) -> anyhow::Result<String> {
    let path = dir.join("SKILL.md");
    let content = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read skill file {}", path.display()))?;
    Ok(strip_frontmatter(&content).to_string())
}

/// Remove a leading YAML frontmatter block (`---` on the first line up to
/// the next `---` line). Files without frontmatter pass through unchanged.
/// Tolerates CRLF line endings.
fn strip_frontmatter(content: &str) -> &str {
    let mut lines = content.split_inclusive('\n');
    let Some(first) = lines.next() else {
        return content;
    };
    if first.trim_end() != "---" {
        return content;
    }
    let mut offset = first.len();
    for line in lines {
        offset += line.len();
        if line.trim_end() == "---" {
            return content[offset..].trim_start_matches(['\r', '\n']);
        }
    }
    // Opening `---` without a closing one: not frontmatter, keep as-is.
    content
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_frontmatter_and_keeps_body() {
        let skill =
            "---\nname: review-subtitles\ndescription: check srt\n---\n# Review\n\nDo the thing.\n";
        assert_eq!(strip_frontmatter(skill), "# Review\n\nDo the thing.\n");
    }

    #[test]
    fn strips_crlf_frontmatter() {
        let skill = "---\r\nname: x\r\n---\r\nbody\r\n";
        assert_eq!(strip_frontmatter(skill), "body\r\n");
    }

    #[test]
    fn passes_through_without_frontmatter() {
        let skill = "# Plain markdown\n\nNo frontmatter here.\n";
        assert_eq!(strip_frontmatter(skill), skill);
    }

    #[test]
    fn unclosed_frontmatter_is_not_stripped() {
        let skill = "---\nname: x\nno closing fence\n";
        assert_eq!(strip_frontmatter(skill), skill);
    }

    #[test]
    fn empty_body_after_frontmatter() {
        assert_eq!(strip_frontmatter("---\nname: x\n---\n"), "");
    }

    #[test]
    fn load_skill_reads_only_skill_md() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.md"),
            "---\nname: demo\n---\n# Demo skill\n",
        )
        .unwrap();
        // A references file next to SKILL.md must NOT be pulled in.
        std::fs::write(dir.path().join("references.md"), "SECRET REFERENCE").unwrap();
        let injected = load_skill(dir.path()).unwrap();
        assert_eq!(injected, "# Demo skill\n");
        assert!(!injected.contains("SECRET REFERENCE"));
    }

    #[test]
    fn load_skill_missing_file_is_error() {
        let dir = tempfile::tempdir().unwrap();
        let err = load_skill(dir.path()).unwrap_err();
        assert!(err.to_string().contains("SKILL.md"));
    }
}
