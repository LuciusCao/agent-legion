//! Bash command guard: reject full-disk scan commands before spawn.
//!
//! Motivation: under parallel agent load, a single agent that cannot find a
//! program (or wants to "explore") may run `find / -name python3`-style
//! scans. One scan is harmless; ten concurrent jobs each scanning the whole
//! filesystem flood fseventsd/Spotlight with change/enumeration events and
//! stall the host (observed in production: fseventsd at 20GB RSS / 140% CPU
//! on a 10-core machine with load average 26).
//!
//! This is a footgun guard, not a security boundary (the OS sandbox is the
//! boundary): detection is a heuristic over shell text and aims at the
//! broad-root recursive scan patterns an LLM agent realistically emits. A
//! blocked command returns a tool error with remediation guidance, which the
//! model reads and adapts to.

use super::ToolError;

/// Roots whose recursive enumeration is considered a full-disk scan.
/// Trailing slashes are normalized before comparison.
const BLOCKED_ROOTS: &[&str] = &[
    "/",
    "/System",
    "/Library",
    "/Users",
    "/private",
    "/Volumes",
    "/Applications",
    "/Network",
    "~",
    "$HOME",
    "${HOME}",
];

/// Commands that recurse by default; any blocked-root argument triggers.
const ALWAYS_RECURSIVE: &[&str] = &["find", "rg", "tree", "du"];
/// Commands that recurse only with -r/-R/--recursive.
const FLAG_RECURSIVE: &[&str] = &["grep", "egrep", "fgrep", "ls"];
/// Leading wrapper tokens skipped when identifying the real command.
const WRAPPERS: &[&str] = &["sudo", "command", "env", "time", "nice", "ionice"];

/// Check `command` for full-disk scan patterns; `Err` blocks execution.
pub fn check(command: &str) -> Result<(), ToolError> {
    for segment in split_segments(command) {
        let tokens: Vec<String> = segment.split_whitespace().map(unquote).collect();
        if let Some((cmd, recursive)) = identify(&tokens) {
            if !recursive {
                continue;
            }
            if let Some(root) = tokens.iter().find_map(|t| blocked_root(t)) {
                return Err(ToolError::CommandBlocked(format!(
                    "`{cmd} {root}` scans from a broad filesystem root. \
                     Full-disk scans flood host file-system indexing \
                     (fseventsd/Spotlight) and stall the machine under \
                     parallel agent load. Search inside the working \
                     directory or a specific subdirectory instead; to \
                     locate an executable use `command -v <name>` \
                     (python/python3 are on PATH)."
                )));
            }
        }
    }
    Ok(())
}

/// Split a shell command into simple-command segments: pipelines, lists,
/// and command substitutions each get their own segment.
fn split_segments(command: &str) -> Vec<String> {
    command
        .replace("$(", "\n")
        .replace('`', "\n")
        .replace(['|', '&', ';', '\n'], "\n")
        .split('\n')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Strip one layer of matching quotes so `"find"` and `"/"` compare equal
/// to their bare forms.
fn unquote(token: &str) -> String {
    token.trim_matches(|c| c == '"' || c == '\'').to_string()
}

/// Identify the real command behind env assignments and wrapper commands.
/// Returns the basename and whether the invocation recurses.
fn identify(tokens: &[String]) -> Option<(String, bool)> {
    let mut rest = tokens;
    loop {
        match rest.first() {
            // Leading VAR=value assignments.
            Some(t)
                if t.contains('=')
                    && t.chars()
                        .next()
                        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
                    && !t.starts_with('-') =>
            {
                rest = &rest[1..];
            }
            Some(t) if WRAPPERS.contains(&basename(t).as_str()) => rest = &rest[1..],
            Some(t) => {
                let name = basename(t);
                if ALWAYS_RECURSIVE.contains(&name.as_str()) {
                    return Some((name, true));
                }
                if FLAG_RECURSIVE.contains(&name.as_str()) {
                    let recursive = rest[1..].iter().any(|arg| is_recursive_flag(arg));
                    return Some((name, recursive));
                }
                return None;
            }
            None => return None,
        }
    }
}

fn basename(token: &str) -> String {
    token.rsplit('/').next().unwrap_or(token).to_string()
}

fn is_recursive_flag(token: &str) -> bool {
    if token == "--recursive" {
        return true;
    }
    // Combined short flags: -r, -R, -rn, -lR, …
    token.starts_with('-')
        && !token.starts_with("--")
        && token[1..].chars().any(|c| c == 'r' || c == 'R')
}

/// Normalize a token and return the matched blocked root, if any.
fn blocked_root(token: &str) -> Option<String> {
    let mut t = token;
    // Redirections (`2>/dev/null`) and flag values never match: they are
    // neither bare roots nor start with a root prefix below.
    while t.len() > 1 && t.ends_with('/') {
        t = &t[..t.len() - 1];
    }
    if BLOCKED_ROOTS.contains(&t) {
        return Some(t.to_string());
    }
    // `~/` or `$HOME/` with nothing after them was handled above; anything
    // deeper (`~/GitHub`) is a scoped path and allowed.
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blocked(command: &str) -> bool {
        check(command).is_err()
    }

    #[test]
    fn blocks_full_disk_find() {
        assert!(blocked("find / -name python3 -type f"));
        assert!(blocked("find / -name 'python3' 2>/dev/null | head -10"));
        assert!(blocked("sudo find / -name python3"));
        assert!(blocked("FOO=bar find /System -name x"));
        assert!(blocked("find /Users/ -name secret"));
        assert!(blocked("find ~ -name x"));
        assert!(blocked("find $HOME -name x"));
        assert!(blocked("/usr/bin/find / -name x"));
        assert!(blocked("echo $(find / -name x)"));
        assert!(blocked("ls -R / | head"));
        assert!(blocked("grep -rn foo /Library"));
        assert!(blocked("rg pattern /Applications"));
        assert!(blocked("du -sh /"));
        assert!(blocked("tree /private"));
    }

    #[test]
    fn allows_scoped_searches() {
        assert!(!blocked("find . -name python3"));
        assert!(!blocked("find /usr/local -name python3 -type f"));
        assert!(!blocked("find /opt/homebrew/bin -name 'python*'"));
        assert!(!blocked("find ~/GitHub -name x"));
        assert!(!blocked("grep -r foo ."));
        assert!(!blocked("ls -la /tmp"));
        assert!(!blocked("ls /usr/local/bin"));
        assert!(!blocked("du -sh ./data"));
        assert!(!blocked("command -v python3"));
        assert!(!blocked("echo find /"));
        assert!(!blocked("python scripts/validate_output.py ."));
    }

    #[test]
    fn error_message_guides_remediation() {
        let err = check("find / -name python3").unwrap_err().to_string();
        assert!(
            err.contains("command -v"),
            "missing remediation hint: {err}"
        );
        assert!(
            err.contains("working directory"),
            "missing scope hint: {err}"
        );
    }
}
