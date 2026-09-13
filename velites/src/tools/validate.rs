//! `validate` tool: mid-run self-check of the working-directory outputs
//! against the skill's output contract (issue #443, design §8; contract
//! location migrated to the skill-root `contract.yaml` in #542, embedded
//! block still honored as the deprecated fallback). No arguments; the
//! engine lives in `crate::contract`. The tool fails informatively when no
//! skill directory declares a contract — silently succeeding would tell
//! the model a lie.

use serde_json::Value;

use super::{ToolContext, ToolOutput};

pub async fn run(_args: &Value, ctx: &ToolContext) -> ToolOutput {
    match crate::contract::first_contract(&ctx.skill_dirs) {
        None => ToolOutput::error(
            "no output contract found in the skill directories \
             (no contract.yaml and no embedded contract block); \
             nothing to validate against"
                .into(),
        ),
        Some(Err(err)) => ToolOutput::error(format!("contract parse error: {err}")),
        Some(Ok(contract)) => {
            let violations = contract.check(&ctx.cwd);
            if violations.is_empty() {
                let mut message = format!("contract ok ({} files checked)", contract.file_count());
                if contract.source() == crate::contract::ContractSource::EmbeddedBlock {
                    message.push_str(
                        "\nnote: contract read from the deprecated embedded block; \
                         migrate to the skill-root contract.yaml",
                    );
                }
                ToolOutput::text(message, false).measured()
            } else {
                let list = violations
                    .iter()
                    .enumerate()
                    .map(|(i, v)| format!("{}) {v}", i + 1))
                    .collect::<Vec<_>>()
                    .join("\n");
                // `measured` keeps the violation listing (is_error = true)
                // in the timing set — the files were fully checked (#469).
                ToolOutput::error(format!("contract violations:\n{list}")).measured()
            }
        }
    }
}
