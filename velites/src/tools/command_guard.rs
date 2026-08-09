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
//!
//! Handled evasions: subshell groups `( … )`, quote/backslash concatenation
//! (`"$HOME"/`, `\/`), find's leading global options (`find -L /`), pattern-
//! via-option forms (`rg --files /`, `grep --regexp=x /`), `cd`-tracked
//! relative paths (`cd / && find ./System`), wrapper commands and their
//! options (sudo/env/nice/timeout/nohup/setsid/stdbuf/xargs), nested shells
//! (`bash -c '…'`, `eval`), shell variable indirection (`D=/; find $D`), and
//! globs with broad literal prefixes (`find /*`).
//!
//! Known gaps (accepted, documented): brace-expansion paths (`find {/,/usr}`),
//! `{ …; }` command groups, `if/then` keyword syntax, function definitions,
//! `pushd`/`builtin cd`, ANSI-C quoting (`$'/'`), xargs stdin-fed paths
//! (`echo / | xargs find`), and case variants on case-insensitive filesystems.
//! Pipe-segment `cd` (`cd / | find .`) is over-blocked on purpose (the real
//! shell runs it in a subshell; blocking err toward safety).

use std::collections::HashMap;

use super::ToolError;

/// Roots whose recursive enumeration is considered a full-disk scan.
/// Trailing slashes are normalized before comparison; the process's $HOME
/// (expanded or not) is blocked on top of this list.
const BLOCKED_ROOTS: &[&str] = &[
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

/// Commands that recurse by default; any blocked-root argument triggers.
const ALWAYS_RECURSIVE: &[&str] = &["find", "rg", "tree", "du"];
/// Commands that recurse only with -r/-R/--recursive.
const FLAG_RECURSIVE: &[&str] = &["grep", "egrep", "fgrep", "ls"];
/// Leading wrapper tokens skipped when identifying the real command.
/// `timeout` is handled specially (it takes a duration positional).
const WRAPPERS: &[&str] = &[
    "sudo", "command", "env", "time", "nice", "ionice", "nohup", "setsid", "stdbuf", "xargs",
];
/// Shells whose `-c <string>` argument is checked recursively.
const SHELLS: &[&str] = &["sh", "bash", "dash", "zsh"];

/// Wrapper options that consume the following token as their value, keyed
/// by wrapper (`nice -n 10` vs `sudo -n` — the same flag differs).
fn wrapper_value_opts(wrapper: &str) -> &'static [&'static str] {
    match wrapper {
        "sudo" => &["-u", "-g", "-h"],
        "nice" | "ionice" => &["-n"],
        "env" => &["-u", "-C", "-S"],
        "stdbuf" => &["-i", "-o", "-e"],
        "xargs" => &["-I", "-n", "-P", "-L", "-s", "-d"],
        "timeout" => &["-k", "-s"],
        _ => &[],
    }
}

/// Check `command` for full-disk scan patterns; `Err` blocks execution.
pub fn check(command: &str) -> Result<(), ToolError> {
    check_inner(command, &mut HashMap::new())
}

fn check_inner(command: &str, vars: &mut HashMap<String, String>) -> Result<(), ToolError> {
    // `cd` in one segment changes the cwd of later segments in the same
    // shell, so `cd / && find .` must be judged against `/`, not the job dir.
    let mut cwd: Option<String> = None;
    for segment in split_segments(command) {
        let tokens: Vec<String> = segment.split_whitespace().map(unquote).collect();
        // Pure assignment segment (`D=/; find $D`): record for expansion.
        if !tokens.is_empty() && tokens.iter().all(|t| is_assignment(t)) {
            for token in &tokens {
                if let Some((name, value)) = token.split_once('=') {
                    vars.insert(name.to_string(), value.to_string());
                }
            }
            continue;
        }
        let Some((cmd, args)) = identify(&tokens) else {
            continue;
        };
        if cmd == "cd" {
            let arg = args.iter().find(|a| !a.starts_with('-'));
            cwd = Some(resolve_cd(cwd.as_deref(), arg.map(String::as_str)));
            continue;
        }
        // Nested shells and eval: recurse into the inner command text.
        if SHELLS.contains(&cmd.as_str()) {
            if let Some(pos) = args.iter().position(|a| a == "-c") {
                if pos + 1 < args.len() {
                    check_inner(&args[pos + 1..].join(" "), vars)?;
                }
            }
            continue;
        }
        if cmd == "eval" {
            check_inner(&args.join(" "), vars)?;
            continue;
        }
        if !is_recursive(&cmd, args) {
            continue;
        }
        let paths = scan_paths(&cmd, args);
        let mut hit = paths
            .iter()
            .map(|p| expand_vars(p, vars))
            .map(|p| resolve_against(&p, cwd.as_deref()))
            .find_map(|p| flagged_path(&p));
        // No explicit path (or `.`) means "scan the current directory",
        // which a preceding `cd` may have moved to a broad root.
        if hit.is_none() && (paths.is_empty() || paths.iter().any(|p| is_dot(p))) {
            if let Some(cwd) = &cwd {
                hit = blocked_root(&normalize(cwd));
            }
        }
        if let Some(root) = hit {
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
    Ok(())
}

/// Split a shell command into simple-command segments: pipelines, lists,
/// command substitutions and subshell groups each get their own segment.
/// Separators inside quotes stay intact so `bash -c "cd / && find ."` keeps
/// its inner command whole for the recursive check; `$(` boundaries drop the
/// opener so the inner text splits as its own segment. `{`/`}` stay
/// untouched so `${HOME}` keeps its variable form for later expansion.
fn split_segments(command: &str) -> Vec<String> {
    let mut segments = Vec::new();
    let mut cur = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut chars = command.chars().peekable();
    while let Some(c) = chars.next() {
        if in_single {
            if c == '\'' {
                in_single = false;
            }
            cur.push(c);
            continue;
        }
        if in_double {
            if c == '"' {
                in_double = false;
            }
            cur.push(c);
            continue;
        }
        match c {
            '\'' => {
                in_single = true;
                cur.push(c);
            }
            '"' => {
                in_double = true;
                cur.push(c);
            }
            '$' if chars.peek() == Some(&'(') => {
                chars.next();
                push_segment(&mut segments, &mut cur);
            }
            '`' | '(' | ')' | '|' | '&' | ';' | '\n' => push_segment(&mut segments, &mut cur),
            _ => cur.push(c),
        }
    }
    push_segment(&mut segments, &mut cur);
    segments
}

fn push_segment(segments: &mut Vec<String>, cur: &mut String) {
    let trimmed = cur.trim();
    if !trimmed.is_empty() {
        segments.push(trimmed.to_string());
    }
    cur.clear();
}

/// Shell words concatenate quoted and unquoted parts (`"$HOME"/x` is one
/// word); quotes and escape backslashes carry no path meaning, so strip
/// them all before comparing tokens.
fn unquote(token: &str) -> String {
    token
        .chars()
        .filter(|c| !matches!(c, '"' | '\'' | '\\'))
        .collect()
}

fn is_assignment(token: &str) -> bool {
    token.contains('=')
        && token
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && !token.starts_with('-')
}

/// Identify the real command behind env assignments and wrapper commands
/// (skipping the wrappers' own options and option values). Returns the
/// basename and the remaining arguments.
fn identify(tokens: &[String]) -> Option<(String, &[String])> {
    let mut rest = tokens;
    loop {
        match rest.first() {
            // Leading VAR=value assignments (apply to this command only).
            Some(t) if is_assignment(t) => rest = &rest[1..],
            Some(t) if basename(t) == "timeout" => {
                rest = &rest[1..];
                rest = skip_options(rest, wrapper_value_opts("timeout"));
                // Plus one positional: the duration.
                if !rest.is_empty() {
                    rest = &rest[1..];
                }
            }
            Some(t) if WRAPPERS.contains(&basename(t).as_str()) => {
                let value_opts = wrapper_value_opts(&basename(t));
                rest = skip_options(&rest[1..], value_opts);
            }
            Some(t) => return Some((basename(t), &rest[1..])),
            None => return None,
        }
    }
}

/// Skip leading option tokens; value-taking options consume the next token
/// too (`env -i find /`, `nice -n 10 du /`).
fn skip_options<'a>(mut rest: &'a [String], value_opts: &[&str]) -> &'a [String] {
    while let Some(opt) = rest.first() {
        if !opt.starts_with('-') {
            break;
        }
        let takes_value = value_opts.contains(&opt.as_str());
        rest = &rest[1..];
        if takes_value && !rest.is_empty() {
            rest = &rest[1..];
        }
    }
    rest
}

fn basename(token: &str) -> String {
    token.rsplit('/').next().unwrap_or(token).to_string()
}

fn is_recursive(cmd: &str, args: &[String]) -> bool {
    if ALWAYS_RECURSIVE.contains(&cmd) {
        return true;
    }
    FLAG_RECURSIVE.contains(&cmd) && args.iter().any(|a| is_recursive_flag(a))
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

/// Extract the scan-root candidates from the argument list. `find` takes
/// paths after its leading global options and before the `-expression`;
/// `rg`/`grep` take PATTERN before the paths unless the pattern/listing
/// comes from an option (`-e`/`--regexp`/`-f`/`--file`/`--files`), in which
/// case every operand is a path candidate. No operands at all means the
/// command scans the current directory.
fn scan_paths<'a>(cmd: &str, args: &'a [String]) -> Vec<&'a str> {
    if cmd == "find" {
        // GNU and BSD find both accept global options before the paths
        // (`find -L / -name x`); BSD `-f` carries a path as its value.
        // Anything else starting with `-` opens the expression and ends the
        // path list.
        let mut i = 0;
        let mut f_paths: Vec<&str> = Vec::new();
        while i < args.len() {
            match args[i].as_str() {
                "-H" | "-L" | "-P" | "-E" | "-X" | "-d" | "-s" | "-x" => i += 1,
                "-f" => {
                    if i + 1 < args.len() {
                        f_paths.push(args[i + 1].as_str());
                    }
                    i += 2;
                }
                "-D" => i += 2,
                a if a.starts_with("-O") => i += 1,
                _ => break,
            }
        }
        let mut paths: Vec<&str> = args[i..]
            .iter()
            .take_while(|a| !a.starts_with('-'))
            .map(String::as_str)
            .collect();
        paths.extend(f_paths);
        return paths;
    }
    let operands: Vec<&str> = args
        .iter()
        .filter(|a| !a.starts_with('-'))
        .map(String::as_str)
        .collect();
    match cmd {
        "rg" | "grep" | "egrep" | "fgrep" => {
            if pattern_via_option(args) {
                operands
            } else {
                operands.into_iter().skip(1).collect()
            }
        }
        _ => operands,
    }
}

/// True when the pattern (or the listing itself) comes from an option
/// rather than a positional PATTERN operand.
fn pattern_via_option(args: &[String]) -> bool {
    args.iter().any(|a| {
        a == "-e"
            || a == "--regexp"
            || a.starts_with("--regexp=")
            || (a.starts_with("-e") && !a.starts_with("--") && a.len() > 2)
            || a == "-f"
            || a == "--file"
            || a.starts_with("--file=")
            || a == "--files"
    })
}

fn is_dot(path: &str) -> bool {
    matches!(path, "." | "./")
}

/// Expand a leading `$VAR`/`${VAR}` from earlier pure-assignment segments.
/// Unknown variables (e.g. `$HOME`) pass through for `normalize` to handle.
fn expand_vars(token: &str, vars: &HashMap<String, String>) -> String {
    let (name, rest) = if let Some(r) = token.strip_prefix("${") {
        match r.split_once('}') {
            Some((n, r)) => (n, r),
            None => return token.to_string(),
        }
    } else if let Some(r) = token.strip_prefix('$') {
        let end = r
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(r.len());
        (&r[..end], &r[end..])
    } else {
        return token.to_string();
    };
    match vars.get(name) {
        Some(value) => format!("{value}{rest}"),
        None => token.to_string(),
    }
}

/// A relative scan path is judged against the tracked cwd (`cd / && find
/// ./System` scans `/System`); absolute/home/variable paths stand alone.
fn resolve_against(path: &str, cwd: Option<&str>) -> String {
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
fn flagged_path(path: &str) -> Option<String> {
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
fn resolve_cd(prev: Option<&str>, arg: Option<&str>) -> String {
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
fn normalize(path: &str) -> String {
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
fn blocked_root(normalized: &str) -> Option<String> {
    if BLOCKED_ROOTS.contains(&normalized) {
        return Some(normalized.to_string());
    }
    let home = normalize("~");
    if normalized == home || normalized == "$HOME" {
        return Some(normalized.to_string());
    }
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
        assert!(blocked("find ${HOME} -name x"));
        assert!(blocked("/usr/bin/find / -name x"));
        assert!(blocked("echo $(find / -name x)"));
        assert!(blocked("ls -R / | head"));
        assert!(blocked("grep -rn foo /Library"));
        assert!(blocked("rg pattern /Applications"));
        assert!(blocked("du -sh /"));
        assert!(blocked("tree /private"));
        assert!(blocked("find /home -name x"));
        assert!(blocked("find /usr / -name x"));
        assert!(blocked("find // -name x"));
        assert!(blocked("find /./System -name x"));
        assert!(blocked("find /System/. -name x"));
    }

    #[test]
    fn blocks_cd_evasion() {
        assert!(blocked("cd / && find . -name python3"));
        assert!(blocked("cd /Users && rg pattern ."));
        assert!(blocked("cd / && cd System && find . -name x"));
        assert!(blocked("cd /Users/.. && find . -name x"));
        assert!(blocked("cd /; du -sh"));
        assert!(blocked("cd /; tree"));
        assert!(blocked("cd / && ls -R"));
        // A scoped cd followed by a scoped scan stays allowed.
        assert!(!blocked("cd /usr/local && find . -name python3"));
        assert!(!blocked("cd / && cd usr/local && find . -name x"));
    }

    #[test]
    fn blocks_cd_relative_paths_and_flags() {
        assert!(blocked("cd / && find ./System -name x"));
        assert!(blocked("cd / && rg foo ./Users"));
        assert!(blocked("cd -P / && find . -name x"));
        assert!(blocked("cd -L / && find . -name x"));
        assert!(blocked("cd -- / && find . -name x"));
    }

    #[test]
    fn blocks_wrapper_option_evasion() {
        assert!(blocked("env -i find / -name x"));
        assert!(blocked("sudo -n find / -name x"));
        assert!(blocked("nice -n 10 du /"));
        assert!(blocked("sudo -u root find / -name x"));
        assert!(blocked("timeout 60 find / -name x"));
        assert!(blocked("timeout -k 5 60 du /"));
        assert!(blocked("nohup find / -name x &"));
        assert!(blocked("setsid find / -name x"));
        assert!(blocked("echo foo | xargs find / -name x"));
    }

    #[test]
    fn blocks_subshell_groups() {
        assert!(blocked("(find / -name x)"));
        assert!(blocked("(cd / && find .)"));
        assert!(blocked("(cd / && find ./System)"));
    }

    #[test]
    fn blocks_quote_and_escape_concatenation() {
        assert!(blocked("find \"$HOME\"/ -name x"));
        assert!(blocked("find \"\"/System -name x"));
        assert!(blocked("find /U\"s\"ers -name x"));
        assert!(blocked("find \\/ -name x"));
        assert!(blocked("find ''/ -name x"));
    }

    #[test]
    fn blocks_find_global_options_before_paths() {
        assert!(blocked("find -L / -name x"));
        assert!(blocked("find -H /System -name x"));
        assert!(blocked("find -P -L /Users -name x"));
        // -f takes a path value (BSD find).
        assert!(blocked("find -f / -name x"));
    }

    #[test]
    fn blocks_pattern_via_option_paths() {
        assert!(blocked("rg --files /"));
        assert!(blocked("rg --regexp=foo /Users"));
        assert!(blocked("grep -r --regexp=foo /"));
        assert!(blocked("grep -r -e foo /Users"));
        assert!(blocked("rg /Users -e foo"));
        // Pattern as a normal operand keeps working.
        assert!(blocked("rg pattern /Applications"));
    }

    #[test]
    fn blocks_nested_shell_and_eval() {
        assert!(blocked("sh -c 'find /'"));
        assert!(blocked("bash -c \"cd / && find .\""));
        assert!(blocked("eval \"find /\""));
        assert!(blocked("bash -c 'sh -c \"find /System\"'"));
    }

    #[test]
    fn blocks_variable_indirection() {
        assert!(blocked("D=/; find $D -name x"));
        assert!(blocked("D=/System; find ${D} -name x"));
        assert!(blocked("D=/Users; find ${D}/.. -name x"));
    }

    #[test]
    fn blocks_broad_glob_prefixes() {
        assert!(blocked("find /* -name x"));
        assert!(blocked("find /[U]sers -name x"));
        assert!(blocked("find ~/* -name x"));
        assert!(!blocked("find ./* -name x"));
        assert!(!blocked("find ./data/*.log -name x"));
    }

    #[test]
    fn blocks_expanded_home() {
        let home = std::env::var("HOME").expect("HOME set in tests");
        assert!(blocked(&format!("find {home} -name x")));
        assert!(blocked(&format!("find {home}/ -name x")));
        assert!(blocked(&format!("find {home}/.. -name x")));
        // Deeper subdirectories of home stay scoped.
        assert!(!blocked(&format!("find {home}/GitHub -name x")));
    }

    #[test]
    fn allows_scoped_searches() {
        assert!(!blocked("find . -name python3"));
        assert!(!blocked("find /usr/local -name python3 -type f"));
        assert!(!blocked("find /opt/homebrew/bin -name 'python*'"));
        assert!(!blocked("find ~/GitHub -name x"));
        assert!(!blocked("grep -r foo ."));
        assert!(!blocked("grep -rn 'foo/bar' ."));
        assert!(!blocked("ls -la /tmp"));
        assert!(!blocked("ls /usr/local/bin"));
        assert!(!blocked("du -sh ./data"));
        assert!(!blocked("command -v python3"));
        assert!(!blocked("echo find /"));
        assert!(!blocked("python scripts/validate_output.py ."));
    }

    #[test]
    fn documents_pipe_cd_overblock() {
        // `cd /` in a pipeline runs in a subshell and does NOT move the cwd
        // of `find .`; blocking it anyway is a deliberate fail-safe choice.
        assert!(blocked("cd / | find ."));
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
