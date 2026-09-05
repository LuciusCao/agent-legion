//! `velites-sandbox`: the OS sandbox wrapper as its own binary (issue #383).
//!
//! The sandbox is worker-image infrastructure, not an agent-runtime feature:
//! code-node execution (EXEC-CODE-003) wraps every child through the same
//! seatbelt/bwrap policy the harness `bash` tool uses. Historically this
//! entry point rode along inside the main `velites` binary (M4 integration
//! convenience); #381 moved runtime executors out of the worker image, and
//! #383 splits the sandbox back out as `velites-sandbox` so the image ships
//! the sandbox without shipping (or auto-detecting) the agent harness.
//!
//! The binary name is deliberately NOT `velites`: worker runtime auto-detect
//! (#254) probes binaries by name (`RUNTIME_CATALOG` matches `velites`
//! exactly), so a differently-named binary keeps the sandbox from
//! re-polluting the runtime declaration surface.
//!
//! Argv compatibility: callers built argv for the main binary as
//! `velites sandbox wrap --cwd ... -- cmd`; this binary accepts both that
//! form (leading `sandbox wrap` tokens are consumed here, mirroring the
//! manual dispatch in the main binary's main.rs) and the bare form
//! (`velites-sandbox --cwd ... -- cmd`), so `shared/code_sandbox.py` needs
//! no per-binary argv branching.

use std::process::ExitCode;

use clap::Parser;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    // Clap-owned flags must reach clap untouched (the pre-clap prefix dispatch
    // below would otherwise swallow `--version`, which #381's registration
    // handshake uses for drift diagnostics).
    let clap_flag = matches!(
        args.get(1).map(String::as_str),
        Some("--version") | Some("-V") | Some("--help") | Some("-h")
    );
    let offset = if !clap_flag && args.get(1).map(String::as_str) == Some("validate") {
        // `velites-sandbox validate --job-dir <dir> [--skill <dir>]...`:
        // same standalone output-contract check as the main binary (#443).
        let parse_args = std::iter::once(args[0].clone()).chain(args.into_iter().skip(2));
        let cli = velites::cli::ValidateCli::parse_from(parse_args);
        return ExitCode::from(velites::contract_gate::validate_exit_code(cli));
    } else if !clap_flag && args.get(1).map(String::as_str) == Some("sandbox") {
        if args.get(2).map(String::as_str) != Some("wrap") {
            eprintln!("error: expected `velites-sandbox [sandbox wrap] --cwd <dir> -- <cmd...>`");
            return ExitCode::from(2);
        }
        3
    } else {
        1
    };
    let parse_args = std::iter::once(args[0].clone()).chain(args.into_iter().skip(offset));
    let cli = velites::cli::SandboxWrapCli::parse_from(parse_args);
    match velites::run_sandbox_wrap(cli) {
        Ok(code) => ExitCode::from(code),
        Err(err) => {
            eprintln!("error: {err:#}");
            ExitCode::from(2)
        }
    }
}
