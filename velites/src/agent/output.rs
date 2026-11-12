use std::path::{Path, PathBuf};

use anyhow::anyhow;

use crate::contract::{Contract, ContractError};
use crate::contract_gate::gate_outcome;
use crate::events::{Event, EventSink, OutputsValidationEvent};
use crate::tools::resolve_in_cwd;

/// A `--require-output` entry after sandbox validation.
pub(super) struct RequiredOutput {
    /// The path as passed on the CLI (reported in `outputs_validation`).
    display: String,
    /// Sandbox-resolved absolute path (existence checks).
    resolved: PathBuf,
}

/// Validate `--require-output` paths against the cwd sandbox (same resolver
/// the tools use); escapes are a harness-side configuration error.
pub(super) fn resolve_required_outputs(
    paths: &[PathBuf],
    cwd: &Path,
) -> anyhow::Result<Vec<RequiredOutput>> {
    paths
        .iter()
        .map(|path| {
            let display = path.to_string_lossy().into_owned();
            let resolved = resolve_in_cwd(cwd, &display)
                .map_err(|err| anyhow!("invalid --require-output `{display}`: {err}"))?;
            Ok(RequiredOutput { display, resolved })
        })
        .collect()
}

/// Declared artifacts that do not exist right now (CLI-relative display form).
pub(super) fn missing_outputs(required: &[RequiredOutput]) -> Vec<String> {
    required
        .iter()
        .filter(|output| !output.resolved.exists())
        .map(|output| output.display.clone())
        .collect()
}

/// Always emit `outputs_validation` when `--require-output` was given (even
/// with an empty `missing` list) so the Host can decide explicitly. `mode`
/// reports whether the contract engine ran (#443); `violations` is empty in
/// existence mode or when every contract rule holds.
pub(super) fn emit_outputs_validation(
    sink: &mut dyn EventSink,
    required: &[RequiredOutput],
    contract: Option<&Result<Contract, ContractError>>,
    cwd: &Path,
) {
    if required.is_empty() {
        return;
    }
    let (mode, violations) = gate_outcome(contract, cwd);
    sink.emit(&Event::OutputsValidation(OutputsValidationEvent {
        missing: missing_outputs(required),
        mode: mode.to_string(),
        violations,
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_required_outputs_reports_missing_by_display_path() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("out")).unwrap();
        let cwd = dir.path().canonicalize().unwrap();

        let required = resolve_required_outputs(&[PathBuf::from("out/result.json")], &cwd).unwrap();
        assert_eq!(required.len(), 1);
        assert_eq!(required[0].display, "out/result.json");
        assert!(required[0].resolved.ends_with("out/result.json"));
        // Not yet written: reported missing under the CLI-relative name.
        assert_eq!(missing_outputs(&required), vec!["out/result.json"]);

        std::fs::write(dir.path().join("out/result.json"), "data").unwrap();
        assert!(missing_outputs(&required).is_empty());
    }

    #[test]
    fn resolve_required_outputs_rejects_sandbox_escapes() {
        // An escaping --require-output path is a caller misconfiguration,
        // never a runtime "missing" entry.
        let dir = tempfile::tempdir().unwrap();
        let cwd = dir.path().canonicalize().unwrap();
        for escape in ["../outside.txt", "/etc/passwd"] {
            // Match instead of unwrap_err: RequiredOutput is not Debug.
            let err = match resolve_required_outputs(&[PathBuf::from(escape)], &cwd) {
                Ok(_) => panic!("escaping --require-output `{escape}` must be rejected"),
                Err(err) => err,
            };
            let message = err.to_string();
            assert!(
                message.contains(&format!("invalid --require-output `{escape}`")),
                "unexpected error: {message}"
            );
            assert!(message.contains("escapes"));
        }
    }
}
