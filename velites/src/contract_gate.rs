//! Gate/subcommand glue around the contract engine (#443): the
//! `--require-output` end-of-run gate outcome, the remediation-turn notice,
//! and the `validate` subcommand both binaries expose for the Host-side
//! recheck. Split out of `contract.rs` for the file-size budget; the engine
//! itself (parse + check) lives there.

use crate::cli::ValidateCli;
use crate::contract::{first_contract, Contract, ContractError};

/// Gate outcome for the `--require-output` upgrade: `("existence", [])`
/// without a contract, `("contract", violations)` with one — including the
/// fail-closed parse-error violation.
pub fn gate_outcome(
    contract: Option<&Result<Contract, ContractError>>,
    job_dir: &std::path::Path,
) -> (&'static str, Vec<String>) {
    match contract {
        None => ("existence", Vec::new()),
        Some(Err(err)) => ("contract", vec![format!("contract parse error: {err}")]),
        Some(Ok(contract)) => (
            "contract",
            contract
                .check(job_dir)
                .iter()
                .map(ToString::to_string)
                .collect(),
        ),
    }
}

/// The single remediation-turn notice, listing every failure class the
/// model still has to fix.
pub fn remediation_message(missing: &[String], violations: &[String]) -> String {
    let mut parts = Vec::new();
    if !missing.is_empty() {
        parts.push(format!(
            "the following declared output artifacts are missing: {}",
            missing.join(", ")
        ));
    }
    if !violations.is_empty() {
        let list = violations
            .iter()
            .enumerate()
            .map(|(i, v)| format!("{}) {v}", i + 1))
            .collect::<Vec<_>>()
            .join("; ");
        parts.push(format!("the output contract is violated: {list}"));
    }
    format!(
        "SYSTEM NOTICE: {}. Write or fix the outputs now, then stop.",
        parts.join("; ")
    )
}

/// `velites validate` / `velites-sandbox validate` implementation. Exit
/// codes: 0 = contract holds (`mode=contract` on stdout) or nothing to check
/// (`mode=existence`, the Host falls back to its legacy check); 1 = contract
/// violations (one per stderr line); 2 = contract parse error or I/O failure
/// (the latter propagates as `Err` for the caller to report).
pub fn run_validate(cli: ValidateCli) -> anyhow::Result<u8> {
    use anyhow::Context;
    let job_dir = cli
        .job_dir
        .canonicalize()
        .with_context(|| format!("failed to canonicalize job dir `{}`", cli.job_dir.display()))?;
    match first_contract(&cli.skill) {
        None => {
            println!("mode=existence");
            Ok(0)
        }
        Some(Err(err)) => {
            eprintln!("contract parse error: {err}");
            Ok(2)
        }
        Some(Ok(contract)) => {
            let violations = contract.check(&job_dir);
            if violations.is_empty() {
                println!("mode=contract");
                Ok(0)
            } else {
                for violation in &violations {
                    eprintln!("{violation}");
                }
                Ok(1)
            }
        }
    }
}

/// Shared bin glue for both binaries: run the validate subcommand and map
/// the result to the process exit code (harness/IO errors reported on
/// stderr become 2).
pub fn validate_exit_code(cli: ValidateCli) -> u8 {
    match run_validate(cli) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("error: {err:#}");
            2
        }
    }
}
