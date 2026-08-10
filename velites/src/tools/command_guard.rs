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
//! (`bash -c '…'`, including combined short-option clusters like
//! `bash -xc '…'`, and `eval`), shell variable indirection (`D=/; find $D`,
//! including assignment builtins `export`/`readonly`/`declare`/`typeset`/
//! `local`), parameter-expansion defaults (`find ${X:-/}`), GNU coreutils
//! with the Homebrew `g` prefix (`gfind`/`ggrep`/`gdu`), `fd` (recursive by
//! default; its first operand is the pattern), and globs with broad literal
//! prefixes (`find /*`).
//!
//! Known gaps (accepted, documented): brace-expansion paths (`find {/,/usr}`),
//! `{ …; }` command groups, `if/then` keyword syntax, function definitions,
//! `pushd`/`builtin cd`, ANSI-C quoting (`$'/'`), xargs stdin-fed paths
//! (`echo / | xargs find`), and case variants on case-insensitive filesystems.
//! `cd` targets are normalized LEXICALLY, never canonicalized: a symlink
//! whose target is a broad root (`ln -s / tmp/link; cd tmp/link && find .`)
//! slips through. `eval`/nested-shell recursion has no depth cap: absurdly
//! deep nesting could overflow the stack (input size makes this impractical
//! for a real model to hit). Pipe-segment `cd` (`cd / | find .`) is
//! over-blocked on purpose (the real shell runs it in a subshell; blocking
//! err toward safety).

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
/// `gfind`/`gdu` are GNU coreutils under their Homebrew `g` prefix; `fd`
/// recurses from its path operand by default.
const ALWAYS_RECURSIVE: &[&str] = &["find", "gfind", "fd", "rg", "tree", "du", "gdu"];
/// Commands that recurse only with -r/-R/--recursive.
const FLAG_RECURSIVE: &[&str] = &["grep", "egrep", "fgrep", "ggrep", "ls"];
/// Builtins whose `VAR=value` arguments assign shell variables: their
/// assignment arguments feed the same variable table as pure-assignment
/// segments (`export R=/; find $R` must record R).
const ASSIGNMENT_BUILTINS: &[&str] = &["export", "readonly", "declare", "typeset", "local"];
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
    // shell, so `cd / && find .` must be judged against `/`, not the job
    // dir. Subshells `( … )` fork the shell state: their `cd` applies to
    // segments inside the group but never leaks to the parent shell, so
    // the tracked cwd is scoped per paren depth.
    let mut cwd_by_depth: Vec<Option<String>> = vec![None];
    for (segment, depth) in split_segments(command) {
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
        // Assignment builtins (`export R=/; find $R`): their `VAR=value`
        // arguments enter the same variable table. Non-assignment arguments
        // (flags, names) carry no value; the builtin itself never scans.
        if tokens
            .first()
            .is_some_and(|t| ASSIGNMENT_BUILTINS.contains(&t.as_str()))
        {
            for token in &tokens[1..] {
                if is_assignment(token) {
                    if let Some((name, value)) = token.split_once('=') {
                        vars.insert(name.to_string(), value.to_string());
                    }
                }
            }
            continue;
        }
        let Some((cmd, args)) = identify(&tokens) else {
            continue;
        };
        if cmd == "cd" {
            let arg = args.iter().find(|a| !a.starts_with('-'));
            let parent = effective_cwd(&cwd_by_depth, depth);
            let new_cwd = resolve_cd(parent.as_deref(), arg.map(String::as_str));
            while cwd_by_depth.len() <= depth {
                cwd_by_depth.push(None);
            }
            cwd_by_depth[depth] = Some(new_cwd);
            continue;
        }
        // Nested shells and eval: recurse into the inner command text.
        if SHELLS.contains(&cmd.as_str()) {
            if let Some(pos) = args.iter().position(|a| is_shell_command_flag(a)) {
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
        let cwd = effective_cwd(&cwd_by_depth, depth);
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

/// The tracked cwd at a paren depth: the nearest explicitly set level,
/// falling back towards the top-level shell (subshell `cd` never leaks
/// outwards; a subshell without its own `cd` inherits the parent's).
fn effective_cwd(cwd_by_depth: &[Option<String>], depth: usize) -> Option<String> {
    (0..=depth.min(cwd_by_depth.len().saturating_sub(1)))
        .rev()
        .find_map(|level| cwd_by_depth[level].clone())
}

/// Split a shell command into simple-command segments with their paren
/// depth: pipelines, lists, command substitutions and subshell groups each
/// get their own segment. Separators inside quotes stay intact — except
/// `$(` and backticks inside DOUBLE quotes, which bash still executes and
/// therefore must open new segments (paren depth + quote state are restored
/// at the matching `)`). `{`/`}` stay untouched so `${HOME}` keeps its
/// variable form for later expansion.
fn split_segments(command: &str) -> Vec<(String, usize)> {
    let mut segments = Vec::new();
    let mut cur = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut depth = 0usize;
    // Quote state to restore when a paren opened inside double quotes closes.
    let mut resume_double: Vec<bool> = Vec::new();
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
            match c {
                '"' => {
                    in_double = false;
                    cur.push(c);
                }
                // Command substitution still runs inside double quotes.
                '$' if chars.peek() == Some(&'(') => {
                    chars.next();
                    push_segment(&mut segments, &mut cur, depth);
                    resume_double.push(true);
                    in_double = false;
                    depth += 1;
                }
                '`' => push_segment(&mut segments, &mut cur, depth),
                _ => cur.push(c),
            }
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
                push_segment(&mut segments, &mut cur, depth);
                resume_double.push(false);
                depth += 1;
            }
            '(' => {
                push_segment(&mut segments, &mut cur, depth);
                resume_double.push(false);
                depth += 1;
            }
            ')' => {
                push_segment(&mut segments, &mut cur, depth);
                depth = depth.saturating_sub(1);
                if resume_double.pop() == Some(true) {
                    in_double = true;
                }
            }
            '`' | '|' | '&' | ';' | '\n' => push_segment(&mut segments, &mut cur, depth),
            _ => cur.push(c),
        }
    }
    push_segment(&mut segments, &mut cur, depth);
    segments
}

fn push_segment(segments: &mut Vec<(String, usize)>, cur: &mut String, depth: usize) {
    let trimmed = cur.trim();
    if !trimmed.is_empty() {
        segments.push((trimmed.to_string(), depth));
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

/// `-c` and combined short-option clusters containing it (`-xc`, `-ec`,
/// `-lc`): real shells accept the cluster and take the NEXT token as the
/// command string. Long options are not a shell `-c` form and stay
/// unrecognized.
fn is_shell_command_flag(token: &str) -> bool {
    token.starts_with('-') && !token.starts_with("--") && token[1..].contains('c')
}

/// Extract the scan-root candidates from the argument list. `find` takes
/// paths after its leading global options and before the `-expression`;
/// `rg`/`grep` take PATTERN before the paths unless the pattern/listing
/// comes from an option (`-e`/`--regexp`/`-f`/`--file`/`--files`), in which
/// case every operand is a path candidate. No operands at all means the
/// command scans the current directory.
fn scan_paths<'a>(cmd: &str, args: &'a [String]) -> Vec<&'a str> {
    if cmd == "find" || cmd == "gfind" {
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
        "rg" | "grep" | "egrep" | "fgrep" | "ggrep" => grep_paths(args),
        // `fd PATTERN [path]…`: the first operand is the pattern, not a path.
        "fd" => operands.into_iter().skip(1).collect(),
        _ => operands,
    }
}

/// rg/grep paths: PATTERN is the first positional operand, unless the
/// pattern (or the listing itself) comes from an option — then every
/// positional is a path candidate. Option values are consumed with their
/// option so `-e foo` never leaks `foo` into the path list, nor does
/// `rg -e / .` mistake the pattern `/` for a scan root.
fn grep_paths(args: &[String]) -> Vec<&str> {
    let mut operands: Vec<&str> = Vec::new();
    let mut pattern_via_option = false;
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        match a {
            "-e" | "--regexp" | "-f" | "--file" => {
                pattern_via_option = true;
                i += 2; // option + its value
            }
            _ if a.starts_with("--regexp=") || a.starts_with("--file=") => {
                pattern_via_option = true;
                i += 1;
            }
            // Attached short forms: -efoo, -ffile.
            _ if (a.starts_with("-e") || a.starts_with("-f"))
                && !a.starts_with("--")
                && a.len() > 2 =>
            {
                pattern_via_option = true;
                i += 1;
            }
            "--files" => {
                pattern_via_option = true;
                i += 1;
            }
            _ if a.starts_with('-') => i += 1,
            _ => {
                operands.push(a);
                i += 1;
            }
        }
    }
    if pattern_via_option {
        operands
    } else {
        operands.into_iter().skip(1).collect()
    }
}

fn is_dot(path: &str) -> bool {
    matches!(path, "." | "./")
}

/// Expand a leading `$VAR`/`${VAR}` from earlier assignment segments.
/// Unknown plain variables (e.g. `$HOME`) pass through for `normalize` to
/// handle. An unknown variable WITH a default/alternative expansion op
/// (`${X:-/}`, `${X-default}`, `${X:=…}`, `${X+…}`) is expanded with that
/// word instead: the real shell substitutes it when X is unset, and passing
/// the raw `$…` token through would let the path slip past `flagged_path`
/// (fail-safe direction).
fn expand_vars(token: &str, vars: &HashMap<String, String>) -> String {
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
fn expansion_default_word(op: &str) -> Option<&str> {
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
    fn blocks_assignment_builtin_indirection() {
        // The first token of an assignment-builtin segment is not a
        // `VAR=value` form; its assignment arguments must still be recorded.
        assert!(blocked("export R=/; find $R -name x"));
        assert!(blocked("export R=/System; find ${R} -name x"));
        assert!(blocked("readonly R=/Users; find $R -name x"));
        assert!(blocked("declare R=/; find $R -name x"));
        assert!(blocked("declare -x R=/; find $R -name x"));
        assert!(blocked("typeset R=/; find $R -name x"));
        assert!(blocked("local R=/; find $R -name x"));
        // Normal use stays allowed.
        assert!(!blocked("export FOO=bar; echo $FOO"));
        assert!(!blocked("export EDITOR=vi PAGER=less; echo done"));
        assert!(!blocked("declare -x FOO=bar; echo $FOO"));
    }

    #[test]
    fn blocks_shell_short_option_cluster() {
        // Real shells accept combined short-option clusters; `-c` inside one
        // still takes the next token as the command string.
        assert!(blocked("bash -xc 'find /'"));
        assert!(blocked("bash -ec 'find /System'"));
        assert!(blocked("bash -lc 'find /'"));
        assert!(blocked("sh -xc 'find /Users'"));
        assert!(blocked("zsh -ec 'du -sh /'"));
        // Plain `-c` keeps working.
        assert!(blocked("bash -c 'find /'"));
        // Normal use stays allowed.
        assert!(!blocked("bash -c 'echo hi'"));
        assert!(!blocked("bash -xc 'echo hi'"));
        assert!(!blocked("bash -x script.sh"));
        assert!(!blocked("bash --norc -c 'echo hi'"));
        assert!(!blocked("sh -c 'find . -name x'"));
    }

    #[test]
    fn blocks_parameter_expansion_defaults() {
        // Unset variable + default/alternative word: the shell substitutes
        // the word, so the guard must judge the path with it applied.
        assert!(blocked("find ${X:-/} -name x"));
        assert!(blocked("find ${X:=/System} -name x"));
        assert!(blocked("find ${X-/Users} -name x"));
        assert!(blocked("find ${X=/Library} -name x"));
        assert!(blocked("find ${X+/} -name x"));
        assert!(blocked("find ${X:+/Applications} -name x"));
        // An explicitly assigned variable still wins over the default.
        assert!(blocked("X=/; find ${X:-/usr} -name x"));
        // Normal default-expansion use stays allowed.
        assert!(!blocked("${EDITOR:-vi} file.txt"));
        assert!(!blocked("echo ${X:-/}"));
        assert!(!blocked("find ${X:-.} -name x"));
        assert!(!blocked("find ${X:-/usr/local} -name x"));
    }

    #[test]
    fn blocks_fd_and_gnu_g_prefixed_scans() {
        // `fd` recurses by default; GNU coreutils carry a `g` prefix under
        // Homebrew on macOS.
        assert!(blocked("fd pattern /"));
        assert!(blocked("fd . /System"));
        assert!(blocked("gfind / -name x"));
        assert!(blocked("gfind -L /Users -name x"));
        assert!(blocked("ggrep -r foo /Library"));
        assert!(blocked("gdu -sh /"));
        // Scoped use stays allowed: fd's first operand is the pattern.
        assert!(!blocked("fd pattern ."));
        assert!(!blocked("fd '^foo' src"));
        assert!(!blocked("fd /")); // pattern "/", scan root is the cwd
        assert!(!blocked("gfind . -name x"));
        assert!(!blocked("ggrep -r foo ."));
        assert!(!blocked("gdu -sh ./data"));
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
    fn blocks_command_substitution_in_double_quotes() {
        // Bash performs command substitution inside double quotes.
        assert!(blocked("echo \"$(find / -name x)\""));
        assert!(blocked("echo \"result: $(cd / && find .)\""));
        assert!(blocked("x=\"$(find /System)\""));
    }

    #[test]
    fn subshell_cd_does_not_leak_to_parent() {
        // The parent shell keeps its own cwd across `( … )`.
        assert!(blocked("cd /; (cd /usr/local); find ."));
        assert!(blocked("cd /; (cd /usr/local); rg foo ."));
        // Conversely, a subshell cd / must not poison a scoped parent.
        assert!(!blocked("cd /usr/local; (cd /); find . -name x"));
        assert!(!blocked("(cd /usr/local); find . -name x"));
    }

    #[test]
    fn pattern_option_values_are_not_paths() {
        // `foo` is the pattern here, not a path: the scan root is the cwd.
        assert!(blocked("cd / && rg -e foo"));
        assert!(blocked("cd / && grep -r -e foo"));
        // And the pattern `/` must not be mistaken for a scan root.
        assert!(!blocked("rg -e / ."));
        assert!(!blocked("rg -e foo ."));
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
