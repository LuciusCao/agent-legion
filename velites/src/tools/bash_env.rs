//! Environment allowlist of the `bash` tool child (#922 R-4), split out of
//! `bash_proc.rs` for the file-size budget.

use std::ffi::OsString;

/// Variables the bash child inherits by exact name (#922 R-4). Everything
/// else in the harness environment — provider credentials referenced from
/// models.json, worker-injected secrets — stays out of the model-driven
/// shell. Aligned with the Host-side code sandbox allowlist
/// (`shared/code_sandbox.py::child_env`) plus a few inert shell basics.
const INHERITED_VARS: &[&str] = &[
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_COLLATE",
    "LC_MESSAGES",
    "LC_NUMERIC",
    "LC_TIME",
    "LC_MONETARY",
    "LC_PAPER",
    "LC_NAME",
    "LC_ADDRESS",
    "LC_TELEPHONE",
    "LC_MEASUREMENT",
    "LC_IDENTIFICATION",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
];

/// Exact-name match only: no prefix rule, so an arbitrarily named injected
/// variable can never ride along with a family of allowed names.
fn is_inherited(name: &str) -> bool {
    INHERITED_VARS.contains(&name)
}

/// The allowlisted subset of the harness environment for the bash child;
/// the caller clears the inherited environment and sets exactly these.
pub(super) fn inherited_env() -> Vec<(OsString, OsString)> {
    std::env::vars_os()
        .filter(|(name, _)| name.to_str().is_some_and(is_inherited))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_allowlist_keeps_basics_and_drops_everything_else() {
        for name in [
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        ] {
            assert!(is_inherited(name), "{name} must be inherited");
        }
        for name in [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "LLM_GATEWAY_TOKEN",
            "AGENT_LEGION_DATABASE_URL",
            "AWS_SECRET_ACCESS_KEY",
            "VELITES_MODELS_PATH",
            "MY_PATH",
            "PYTHON_API_TOKEN",
            "LC_API_TOKEN",
            "PYTHONSTARTUP",
            "path",
        ] {
            assert!(!is_inherited(name), "{name} must not be inherited");
        }
    }
}
