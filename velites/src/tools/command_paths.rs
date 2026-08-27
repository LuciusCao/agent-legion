//! Path judgment helpers for the bash command guard (full-disk scan
//! prevention): normalization, blocked-root matching, cwd resolution, glob
//! prefixing, and shell variable expansion for path tokens. Split from
//! ``command_guard.rs`` for the file size budget (#202).

use std::collections::HashMap;

/// Roots whose recursive enumeration is considered a full-disk scan.
/// Trailing slashes are normalized before comparison; the process's $HOME
/// (expanded or not) is blocked on top of this list.
pub(super) const BLOCKED_ROOTS: &[&str] = &[
    "/",
    "/System",
    "/Library",
    "/Users",
    "/home",
    "/private",
    "/Volumes",
    "/Applications",
    "/Network",
];

pub(super) fn is_dot(path: &str) -> bool {
    matches!(path, "." | "./")
}

/// Expand a leading `$VAR`/`${VAR}` from earlier assignment segments.
/// Unknown plain variables (e.g. `$HOME`) pass through for `normalize` to
/// handle. An unknown variable WITH a default/alternative expansion op
/// (`${X:-/}`, `${X-default}`, `${X:=…}`, `${X+…}`) is expanded with that
/// word instead: the real shell substitutes it when X is unset, and passing
/// the raw `$…` token through would let the path slip past `flagged_path`
/// (fail-safe direction).
pub(super) fn expand_vars(token: &str, vars: &HashMap<String, String>) -> String {
    if let Some(r) = token.strip_prefix("${") {
        let Some((inner, rest)) = r.split_once('}') else {
            return token.to_string();
        };
        // Split the variable name off the expansion operator (`:-/`, `=x`, …).
        let name_end = inner
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(inner.len());
        let (name, op) = inner.split_at(name_end);
        if let Some(value) = vars.get(name) {
            return format!("{value}{rest}");
        }
        if let Some(word) = expansion_default_word(op) {
            return format!("{word}{rest}");
        }
        return token.to_string();
    }
    if let Some(r) = token.strip_prefix('$') {
        let end = r
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(r.len());
        let (name, rest) = (&r[..end], &r[end..]);
        return match vars.get(name) {
            Some(value) => format!("{value}{rest}"),
            None => token.to_string(),
        };
    }
    token.to_string()
}

/// The word a `${VAR<op>word}` expansion can substitute: `:-`/`-`/`:=`/`=`
/// (default when VAR is unset) and `:+`/`+` (alternative when VAR is SET —
/// fail-safe judges the path with it either way). Other operators (`:?`,
/// `${#…}`, …) have no path-shaped word: the token is kept as-is.
pub(super) fn expansion_default_word(op: &str) -> Option<&str> {
    // Two-character operators must be tried before their one-character
    // prefixes.
    for prefix in [":-", ":=", ":+", "-", "=", "+"] {
        if let Some(word) = op.strip_prefix(prefix) {
            return Some(word);
        }
    }
    None
}

/// A relative scan path is judged against the tracked cwd (`cd / && find
/// ./System` scans `/System`); absolute/home/variable paths stand alone.
pub(super) fn resolve_against(path: &str, cwd: Option<&str>) -> String {
    if path.starts_with('/') || path.starts_with('~') || path.starts_with('$') {
        return path.to_string();
    }
    match cwd {
        Some(cwd) if !cwd.is_empty() => format!("{cwd}/{path}"),
        _ => path.to_string(),
    }
}

/// Judge one candidate path: globs are judged by their literal prefix
/// (`find /*` scans root-level entries), everything else by the normalized
/// path against the blocked roots.
pub(super) fn flagged_path(path: &str) -> Option<String> {
    if let Some(pos) = path.find(['*', '?', '[']) {
        let raw_prefix = &path[..pos];
        let prefix = raw_prefix.trim_end_matches('/');
        if prefix == "." {
            return None;
        }
        if prefix.is_empty() {
            // `/*` globs root-level entries; `*`/`./*` stays in the cwd.
            return if raw_prefix.starts_with('/') {
                Some("/".to_string())
            } else {
                None
            };
        }
        return blocked_root(&normalize(prefix));
    }
    blocked_root(&normalize(path))
}

/// Track the shell cwd across `cd` segments; a bare `cd` goes home, a
/// relative `cd` resolves against the previous tracked cwd (if any).
pub(super) fn resolve_cd(prev: Option<&str>, arg: Option<&str>) -> String {
    let arg = arg.unwrap_or("$HOME");
    let joined = if arg.starts_with('/') || arg.starts_with('~') || arg.starts_with('$') {
        arg.to_string()
    } else if let Some(prev) = prev {
        format!("{prev}/{arg}")
    } else {
        // Relative cd from the (unknown, scoped) job dir: nothing to track.
        return String::new();
    };
    normalize(&joined)
}

/// Lexically normalize a path: expand `~`/`$HOME`/`${HOME}` to the real
/// home (kept as the literal `$HOME` marker when HOME is unset), strip
/// trailing slashes, and resolve `.`/`..` components so `/Users/..`
/// compares equal to `/`.
pub(super) fn normalize(path: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_else(|_| "$HOME".to_string());
    let expanded = match path {
        "~" => home,
        p if p.starts_with("~/") => format!("{home}/{}", &p[2..]),
        "$HOME" | "${HOME}" => home,
        p if p.starts_with("$HOME/") => format!("{home}/{}", &p[6..]),
        p if p.starts_with("${HOME}/") => format!("{home}/{}", &p[8..]),
        p => p.to_string(),
    };
    let mut parts: Vec<&str> = Vec::new();
    for part in expanded.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            p => parts.push(p),
        }
    }
    let joined = parts.join("/");
    if expanded.starts_with('/') {
        format!("/{joined}")
    } else {
        joined
    }
}

/// Return the matched blocked root for a normalized path, if any. The
/// process's own $HOME (e.g. `/Users/alice`, `/root`) is blocked in
/// addition to the static list; deeper subdirectories stay allowed.
pub(super) fn blocked_root(normalized: &str) -> Option<String> {
    if BLOCKED_ROOTS.contains(&normalized) {
        return Some(normalized.to_string());
    }
    let home = normalize("~");
    if normalized == home || normalized == "$HOME" {
        return Some(normalized.to_string());
    }
    None
}
