//! Skill loading: read the SKILL.md of each explicitly passed `--skill`
//! directory. This reads exactly one file per flag — there is deliberately
//! no directory scanning or discovery of any kind (zero-auto-discovery
//! invariant, design §5).

use std::path::Path;

use anyhow::Context;

pub fn load_skill(dir: &Path) -> anyhow::Result<String> {
    let path = dir.join("SKILL.md");
    let content = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read skill file {}", path.display()))?;
    Ok(content)
}
