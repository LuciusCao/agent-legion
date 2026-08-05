//! OS-level filesystem sandbox for the `bash` tool (design §5, M4.5).
//!
//! The read/write tools already canonicalize paths in-process; this module is
//! the second layer: the bash child (and everything it forks) is wrapped in an
//! OS sandbox so a prompt-level mistake cannot scan or mutate the host.
//!
//! Backends:
//!
//! - macOS: `sandbox-exec` with a seatbelt profile generated at startup
//!   (`deny default`; reads allowed for system paths + cwd/session/skills +
//!   the probed python3 venv root and install prefix, writes only for
//!   cwd/session/$TMPDIR//tmp plus /dev).
//! - Linux: `bubblewrap` (`--ro-bind / /` read-only root, tmpfs on /tmp,
//!   read-write binds for cwd/session/$TMPDIR).
//!
//! Fail-closed: [`Sandbox::new`] probes the backend and returns an error when
//! it is unavailable; the harness refuses to start instead of degrading to an
//! unsandboxed run. The only escape hatch is the explicit `--no-sandbox` flag.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context};

/// OS-level filesystem sandbox wrapping the bash tool's child process.
pub struct Sandbox {
    backend: Backend,
}

enum Backend {
    /// macOS seatbelt profile text, passed to `sandbox-exec -p`.
    #[cfg(target_os = "macos")]
    Seatbelt(String),
    /// Linux bubblewrap policy; wrap mode and the bash tool diverge here.
    #[cfg(target_os = "linux")]
    Bwrap(BwrapPolicy),
}

/// Linux bubblewrap policy variants. The bash tool keeps its historical
/// policy (read-only `/` bind, shared network namespace); `sandbox wrap`
/// gets the strict one (selective read-only binds, private pid namespace,
/// isolated network unless allowed).
#[cfg(target_os = "linux")]
enum BwrapPolicy {
    BashTool {
        read_write: Vec<PathBuf>,
    },
    Wrap {
        read_only: Vec<PathBuf>,
        read_write: Vec<PathBuf>,
        allow_network: bool,
    },
}

/// Which caller is building the sandbox: the bash tool (legacy policy) or
/// the generic `sandbox wrap` subcommand (strict policy).
#[derive(Clone, Copy, PartialEq, Eq)]
enum SandboxMode {
    BashTool,
    Wrap,
}

/// Extra policy knobs for `velites sandbox wrap` (generic command wrapping,
/// e.g. the custom node code child): the bash tool keeps its own fixed
/// policy via [`Sandbox::new`].
#[derive(Default)]
pub struct WrapOptions {
    /// Additional read-write roots (canonicalized at build time).
    pub read_write: Vec<PathBuf>,
    /// Additional read-only roots (Linux needs none: the read-only `/` bind
    /// covers every read-only location).
    pub read_only: Vec<PathBuf>,
    /// Outbound+inbound network is denied by default (macOS: no network rule
    /// in the seatbelt profile; Linux: `--unshare-net`); this flag allows it.
    pub allow_network: bool,
    /// Directories whose LISTING is allowed without making their contents
    /// readable (seatbelt `literal`-only read-data): Python's import system
    /// lists each PYTHONPATH entry, so the parents of read roots need this.
    /// Linux needs nothing: bwrap creates the bind parents in the namespace.
    pub list_only: Vec<PathBuf>,
}

impl Sandbox {
    /// Collect the allowed paths and probe the platform backend.
    ///
    /// `cwd` is the job directory (read-write), `session_dir` the
    /// `--session-dir` (read-write), `skill_dirs` the explicit `--skill`
    /// directories (read-only; Linux covers them via the read-only `/` bind).
    pub fn new(
        cwd: &Path,
        session_dir: Option<&Path>,
        skill_dirs: &[PathBuf],
    ) -> anyhow::Result<Self> {
        let read_write = collect_read_write(cwd, session_dir)?;
        let options = WrapOptions {
            read_only: skill_dirs.to_vec(),
            ..WrapOptions::default()
        };
        Self::build(read_write, &options, SandboxMode::BashTool)
    }

    /// Wrap an arbitrary command (the `sandbox wrap` subcommand): `cwd` is
    /// read-write, everything else comes from `options`. Fail-closed like
    /// [`Sandbox::new`]: an unavailable backend is an error, never a
    /// degrading to an unsandboxed run.
    pub fn for_wrap(cwd: &Path, options: &WrapOptions) -> anyhow::Result<Self> {
        let mut read_write = collect_read_write(cwd, None)?;
        for dir in &options.read_write {
            let canonical = dir.canonicalize().with_context(|| {
                format!("failed to canonicalize write root `{}`", dir.display())
            })?;
            if !read_write.contains(&canonical) {
                read_write.push(canonical);
            }
        }
        Self::build(read_write, options, SandboxMode::Wrap)
    }

    #[cfg(target_os = "macos")]
    fn build(
        read_write: Vec<PathBuf>,
        options: &WrapOptions,
        mode: SandboxMode,
    ) -> anyhow::Result<Self> {
        probe_macos()?;
        let mut read_only = macos_system_read_paths();
        for dir in &options.read_only {
            read_only.push(dir.canonicalize().with_context(|| {
                format!("failed to canonicalize read root `{}`", dir.display())
            })?);
        }
        // Design §8: python3 must run inside the sandbox (skill scripts).
        // uv/Homebrew interpreters live outside the system read list and
        // load libpython via @rpath from their install prefix, and a venv
        // python dies at startup when pyvenv.cfg is unreadable — whitelist
        // both READ-ONLY. A failed probe is silently skipped (system
        // pythons are covered by macos_system_read_paths already).
        for root in python_read_roots() {
            if !read_only.contains(&root) {
                read_only.push(root);
            }
        }
        let mut list_only = Vec::new();
        if mode == SandboxMode::Wrap {
            for root in options.read_only.iter().chain(options.read_write.iter()) {
                if let Some(parent) = root.parent() {
                    if !list_only.contains(&parent.to_path_buf()) {
                        list_only.push(parent.to_path_buf());
                    }
                }
            }
        }
        Ok(Self {
            backend: Backend::Seatbelt(seatbelt_profile_opts(
                &read_only,
                &read_write,
                &list_only,
                options.allow_network,
                // Wrap mode confines signals to the sandboxed process itself;
                // the bash tool keeps the global allow it has always had.
                mode == SandboxMode::Wrap,
            )),
        })
    }

    #[cfg(target_os = "linux")]
    fn build(
        read_write: Vec<PathBuf>,
        options: &WrapOptions,
        mode: SandboxMode,
    ) -> anyhow::Result<Self> {
        probe_linux()?;
        let policy = match mode {
            // Read-only extras need no extra bind: the read-only `/` bind
            // already covers every read-only location.
            SandboxMode::BashTool => BwrapPolicy::BashTool { read_write },
            SandboxMode::Wrap => {
                let mut read_only = Vec::new();
                for dir in &options.read_only {
                    read_only.push(dir.canonicalize().with_context(|| {
                        format!("failed to canonicalize read root `{}`", dir.display())
                    })?);
                }
                BwrapPolicy::Wrap {
                    read_only,
                    read_write,
                    allow_network: options.allow_network,
                }
            }
        };
        Ok(Self {
            backend: Backend::Bwrap(policy),
        })
    }

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    fn build(
        read_write: Vec<PathBuf>,
        options: &WrapOptions,
        _mode: SandboxMode,
    ) -> anyhow::Result<Self> {
        let _ = (read_write, options);
        Err(anyhow!(
            "no filesystem sandbox backend on this platform (supported: macOS seatbelt, Linux bubblewrap)"
        ))
    }

    /// Wrap an argv (e.g. `["bash", "-c", command]`) in the sandbox.
    /// Returns `(program, argv)` to spawn.
    pub fn wrap(&self, inner: &[String]) -> (String, Vec<String>) {
        match &self.backend {
            #[cfg(target_os = "macos")]
            Backend::Seatbelt(profile) => {
                let mut argv = vec!["-p".to_string(), profile.clone()];
                argv.extend(inner.iter().cloned());
                ("sandbox-exec".to_string(), argv)
            }
            #[cfg(target_os = "linux")]
            Backend::Bwrap(policy) => ("bwrap".to_string(), bwrap_argv_for(policy, inner)),
        }
    }
}

/// Read-write roots: cwd, session dir, `$TMPDIR` (canonicalized — on macOS
/// `/var/folders/...` resolves to `/private/var/folders/...`), and `/tmp`.
fn collect_read_write(cwd: &Path, session_dir: Option<&Path>) -> anyhow::Result<Vec<PathBuf>> {
    let mut paths: Vec<PathBuf> = Vec::new();
    let mut push = |path: PathBuf| {
        if !paths.contains(&path) {
            paths.push(path);
        }
    };
    push(cwd.canonicalize().with_context(|| {
        format!(
            "failed to canonicalize working directory `{}`",
            cwd.display()
        )
    })?);
    if let Some(dir) = session_dir {
        push(
            dir.canonicalize().with_context(|| {
                format!("failed to canonicalize session dir `{}`", dir.display())
            })?,
        );
    }
    push(canonical_or_raw(std::env::temp_dir()));
    push(canonical_or_raw(PathBuf::from("/tmp")));
    Ok(paths)
}

fn canonical_or_raw(path: PathBuf) -> PathBuf {
    path.canonicalize().unwrap_or(path)
}

/// Probe PATH for `python3` (manual `which` semantics — no subprocess) and
/// collect the READ-ONLY roots the seatbelt profile must whitelist for that
/// interpreter to actually start (design §8: python3 must run inside the
/// sandbox). Up to two roots:
///
/// - the venv root, when the PATH entry is a venv (`<venv>/bin/python3` with
///   `<venv>/pyvenv.cfg`): CPython's site.py stats pyvenv.cfg (metadata is
///   globally allowed) and then OPENs it — outside the whitelist that is a
///   fatal EPERM at interpreter startup. Whitelisting the venv root also
///   keeps its site-packages importable.
/// - the base install prefix (`<prefix>/bin/python3.x` → `<prefix>`),
///   canonicalized through .venv-style symlinks: uv/Homebrew interpreters
///   load libpython via @rpath from the prefix.
///
/// Empty when no python3 is on PATH or it is a system python (already
/// covered by `macos_system_read_paths`). Only `python3` is probed — design
/// §8 promises exactly that, nothing more. Linux needs no equivalent: the
/// bwrap read-only `/` bind already covers every interpreter location.
#[cfg(any(target_os = "macos", test))]
fn python_read_roots() -> Vec<PathBuf> {
    let Some(path_var) = std::env::var_os("PATH") else {
        return Vec::new();
    };
    python_read_roots_from_path(&path_var)
}

/// PATH-value-taking core of [`python_read_roots`] (tests inject a
/// controlled PATH instead of mutating the process-global one).
#[cfg(any(target_os = "macos", test))]
fn python_read_roots_from_path(path_var: &std::ffi::OsStr) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    let mut push = |path: PathBuf| {
        if !roots.contains(&path) {
            roots.push(path);
        }
    };
    let Some(candidate) = find_executable_on_path(path_var, "python3") else {
        return roots;
    };
    // Venv detection uses the UNRESOLVED candidate path: the whole point of
    // `.venv/bin/python3` is that it is a symlink, and pyvenv.cfg sits next
    // to its bin dir.
    if let Some(venv) = candidate.parent().and_then(Path::parent) {
        if venv.join("pyvenv.cfg").is_file() {
            if let Ok(venv) = venv.canonicalize() {
                push(venv);
            }
        }
    }
    if let Ok(canonical) = candidate.canonicalize() {
        if let Some(prefix) = python_prefix_from_canonical(&canonical) {
            push(prefix);
        }
    }
    roots
}

/// First executable named `name` on the given PATH value, UNRESOLVED (the
/// caller needs the symlink path for venv detection and canonicalizes
/// separately for the install prefix).
#[cfg(any(target_os = "macos", test))]
fn find_executable_on_path(path_var: &std::ffi::OsStr, name: &str) -> Option<PathBuf> {
    std::env::split_paths(path_var)
        .map(|dir| dir.join(name))
        .find(|candidate| is_executable(candidate))
}

/// Install prefix of a canonicalized interpreter: the parent of its `bin`
/// directory. Defensive guards — the prefix must exist AND its path must
/// contain "python": otherwise a `/usr/bin/python3` would put `/usr` on the
/// whitelist (already readable via the system list, and far broader than
/// this probe is meant to grant).
#[cfg(any(target_os = "macos", test))]
fn python_prefix_from_canonical(canonical: &Path) -> Option<PathBuf> {
    let prefix = canonical.parent()?.parent()?;
    if !prefix.is_dir() {
        return None;
    }
    if !prefix.to_string_lossy().contains("python") {
        return None;
    }
    Some(prefix.to_path_buf())
}

#[cfg(any(target_os = "macos", test))]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    // fs::metadata follows symlinks, so a .venv/bin/python3 symlink is
    // judged by the mode of the real interpreter.
    std::fs::metadata(path)
        .map(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// System locations a process must be able to READ to execute at all
/// (binaries, dyld cache, linker config, device nodes, Homebrew prefix).
#[cfg(target_os = "macos")]
fn macos_system_read_paths() -> Vec<PathBuf> {
    [
        "/usr",
        "/bin",
        "/sbin",
        "/System",
        "/Library",
        "/private/etc",
        "/private/var/db/dyld",
        "/dev",
        "/opt/homebrew",
    ]
    .iter()
    .map(PathBuf::from)
    .filter_map(|path| path.canonicalize().ok())
    .collect()
}

/// Escape a path for embedding in a seatbelt profile string literal.
#[cfg(target_os = "macos")]
fn seatbelt_escape(path: &Path) -> String {
    path.display()
        .to_string()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
}

/// Generate the seatbelt profile.
///
/// Structure (validated empirically on macOS 15):
///
/// - `file-read-metadata` is allowed GLOBALLY: with it restricted, dyld /
///   libsystem kill the process at exec (abort/bus error) before `main`.
///   Metadata (stat) alone cannot read file contents or directory entries.
/// - `file-read-data` is restricted to the allowlist below, which must also
///   contain `(literal "/")` — the root directory is opened at process
///   startup. Read-data denial is what blocks content scanning (`readdir`
///   and `open(O_RDONLY)` outside the roots fail with EPERM).
/// - Both `literal` (the directory itself) and `subpath` (its contents) are
///   listed per root: seatbelt treats them as distinct filters.
/// - Read-write roots are also readable.
/// - Network is denied by default (`deny default` covers it); `allow_network`
///   appends an explicit outbound+inbound allow for commands that must talk
///   to a service (e.g. a custom node reaching the CMS).
/// - `signal_self_only` (`sandbox wrap` mode) narrows the global signal
///   allow to `(target self)`; the bash tool keeps the global allow.
#[cfg(target_os = "macos")]
fn seatbelt_profile_opts(
    read_only: &[PathBuf],
    read_write: &[PathBuf],
    list_only: &[PathBuf],
    allow_network: bool,
    signal_self_only: bool,
) -> String {
    let mut profile = String::from("(version 1)\n(deny default)\n");
    // What a process and its children need to run at all (no filesystem effect).
    profile.push_str("(allow process-exec)\n(allow process-fork)\n");
    if signal_self_only {
        profile.push_str("(allow signal (target self))\n");
    } else {
        profile.push_str("(allow signal)\n");
    }
    profile.push_str("(allow sysctl-read)\n");
    profile.push_str("(allow file-read-metadata)\n");

    let emit = |path: &Path, profile: &mut String| {
        let escaped = seatbelt_escape(path);
        profile.push_str(&format!("  (literal \"{escaped}\")\n"));
        profile.push_str(&format!("  (subpath \"{escaped}\")\n"));
    };

    profile.push_str("(allow file-read-data\n  (literal \"/\")\n");
    for path in read_only {
        emit(path, &mut profile);
    }
    for path in read_write {
        emit(path, &mut profile);
    }
    // List-only grants: a bare literal allows readdir on the directory but
    // keeps every file inside unreadable.
    for path in list_only {
        profile.push_str(&format!("  (literal \"{}\")\n", seatbelt_escape(path)));
    }
    profile.push_str(")\n");

    profile.push_str("(allow file-write*\n  (subpath \"/dev\")\n");
    for path in read_write {
        emit(path, &mut profile);
    }
    profile.push_str(")\n");
    if allow_network {
        profile.push_str("(allow network-outbound)\n(allow network-inbound)\n");
    }
    profile
}

/// `bwrap` argv: everything read-only except the read-write roots. `/tmp`
/// becomes an empty tmpfs (scratch writes stay off the host); read-write
/// binds come after it because later mounts win. `unshare_net` additionally
/// isolates the network namespace (the `sandbox wrap` default; the bash tool
/// keeps the shared namespace it has always had).
#[cfg(any(target_os = "linux", test))]
fn bwrap_argv_opts(read_write: &[PathBuf], inner: &[String], unshare_net: bool) -> Vec<String> {
    let mut argv: Vec<String> = vec!["--die-with-parent".into()];
    if unshare_net {
        argv.push("--unshare-net".into());
    }
    argv.extend([
        "--ro-bind".into(),
        "/".into(),
        "/".into(),
        "--dev".into(),
        "/dev".into(),
        "--proc".into(),
        "/proc".into(),
    ]);
    if read_write.iter().any(|path| path == Path::new("/tmp")) {
        argv.extend(["--tmpfs".into(), "/tmp".into()]);
    }
    for path in read_write {
        if path == Path::new("/tmp") {
            continue;
        }
        let display = path.display().to_string();
        argv.extend(["--bind".into(), display.clone(), display]);
    }
    argv.push("--".into());
    argv.extend(inner.iter().cloned());
    argv
}

/// `bwrap` argv with the bash tool's shared-network policy.
#[cfg(any(target_os = "linux", test))]
fn bwrap_argv(read_write: &[PathBuf], inner: &[String]) -> Vec<String> {
    bwrap_argv_opts(read_write, inner, false)
}

/// Dispatch the bwrap argv per policy (bash tool vs `sandbox wrap`).
#[cfg(target_os = "linux")]
fn bwrap_argv_for(policy: &BwrapPolicy, inner: &[String]) -> Vec<String> {
    match policy {
        BwrapPolicy::BashTool { read_write } => bwrap_argv_opts(read_write, inner, false),
        BwrapPolicy::Wrap {
            read_only,
            read_write,
            allow_network,
        } => bwrap_wrap_argv(read_only, read_write, inner, *allow_network),
    }
}

/// `sandbox wrap` bwrap argv (strict policy): selective read-only binds
/// instead of a blanket `/` bind, a private pid namespace with its own
/// /proc, and an isolated network namespace unless the caller opted in.
#[cfg(any(target_os = "linux", test))]
fn bwrap_wrap_argv(
    read_only: &[PathBuf],
    read_write: &[PathBuf],
    inner: &[String],
    allow_network: bool,
) -> Vec<String> {
    let mut argv: Vec<String> = vec!["--die-with-parent".into(), "--unshare-pid".into()];
    if !allow_network {
        argv.push("--unshare-net".into());
    }
    // Read-only system roots a binary needs to start; missing ones are
    // skipped (e.g. /lib64 on some distros).
    for system_root in ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt"] {
        if Path::new(system_root).is_dir() {
            argv.extend(["--ro-bind".into(), system_root.into(), system_root.into()]);
        }
    }
    for path in read_only {
        let display = path.display().to_string();
        argv.extend(["--ro-bind".into(), display.clone(), display]);
    }
    argv.extend(["--dev".into(), "/dev".into()]);
    // Private /proc: only meaningful (and safe to expose) inside the new pid
    // namespace created by --unshare-pid.
    argv.extend(["--proc".into(), "/proc".into()]);
    if read_write.iter().any(|path| path == Path::new("/tmp")) {
        argv.extend(["--tmpfs".into(), "/tmp".into()]);
    }
    for path in read_write {
        if path == Path::new("/tmp") {
            continue;
        }
        let display = path.display().to_string();
        argv.extend(["--bind".into(), display.clone(), display]);
    }
    argv.push("--".into());
    argv.extend(inner.iter().cloned());
    argv
}

#[cfg(target_os = "macos")]
fn probe_macos() -> anyhow::Result<()> {
    let status = std::process::Command::new("sandbox-exec")
        .args(["-p", "(version 1) (allow default)", "/usr/bin/true"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .context("sandbox-exec not found")?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("sandbox-exec probe failed: {status}"))
    }
}

#[cfg(target_os = "linux")]
fn probe_linux() -> anyhow::Result<()> {
    let status = std::process::Command::new("bwrap")
        .arg("--version")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .context("bwrap not found")?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("bwrap probe failed: {status}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inner() -> Vec<String> {
        vec!["bash".into(), "-c".into(), "echo hi".into()]
    }

    #[test]
    fn bwrap_argv_read_only_root_plus_write_binds() {
        let rw = vec![
            PathBuf::from("/job/dir"),
            PathBuf::from("/job/dir/session"),
            PathBuf::from("/tmp"),
        ];
        let argv = bwrap_argv(&rw, &inner());
        let joined = argv.join(" ");
        assert!(joined.contains("--die-with-parent"));
        assert!(joined.contains("--ro-bind / /"));
        assert!(
            joined.contains("--tmpfs /tmp"),
            "tmpfs /tmp missing: {joined}"
        );
        // /tmp is the tmpfs, never a bind.
        assert!(
            !joined.contains("--bind /tmp"),
            "unexpected /tmp bind: {joined}"
        );
        assert!(joined.contains("--bind /job/dir /job/dir"));
        assert!(joined.contains("--bind /job/dir/session /job/dir/session"));
        // tmpfs must precede the binds (later mounts win).
        assert!(
            argv.iter().position(|a| a == "--tmpfs").unwrap()
                < argv.iter().position(|a| a == "--bind").unwrap()
        );
        // `--` separates bwrap flags from the wrapped command.
        let sep = argv.iter().position(|a| a == "--").unwrap();
        assert_eq!(&argv[sep + 1..], inner().as_slice());
    }

    #[test]
    fn bwrap_argv_without_tmp_dir_has_no_tmpfs() {
        let argv = bwrap_argv(&[PathBuf::from("/job")], &inner());
        assert!(!argv.iter().any(|a| a == "--tmpfs"));
    }

    #[test]
    fn bwrap_wrap_argv_selective_binds_and_private_namespaces() {
        let rw = vec![PathBuf::from("/job/dir"), PathBuf::from("/tmp")];
        let ro = vec![PathBuf::from("/repo/server"), PathBuf::from("/opt/venv")];
        let argv = bwrap_wrap_argv(&ro, &rw, &inner(), false);
        let joined = argv.join(" ");
        assert!(joined.contains("--unshare-pid"));
        assert!(joined.contains("--unshare-net"));
        // No blanket root bind in wrap mode.
        assert!(
            !joined.contains("--ro-bind / /"),
            "blanket root bind leaked: {joined}"
        );
        assert!(joined.contains("--ro-bind /usr /usr"));
        assert!(joined.contains("--ro-bind /repo/server /repo/server"));
        assert!(joined.contains("--ro-bind /opt/venv /opt/venv"));
        assert!(joined.contains("--bind /job/dir /job/dir"));
        assert!(joined.contains("--tmpfs /tmp"));
        // Network opt-in drops only the net unshare; pid stays private.
        let allowed = bwrap_wrap_argv(&ro, &rw, &inner(), true).join(" ");
        assert!(!allowed.contains("--unshare-net"));
        assert!(allowed.contains("--unshare-pid"));
    }

    #[test]
    fn bwrap_argv_opts_unshares_network_only_when_requested() {
        let shared = bwrap_argv_opts(&[PathBuf::from("/job")], &inner(), false);
        assert!(!shared.iter().any(|a| a == "--unshare-net"));
        let isolated = bwrap_argv_opts(&[PathBuf::from("/job")], &inner(), true);
        assert!(isolated.iter().any(|a| a == "--unshare-net"));
        // Network isolation lands before the read-only root bind.
        let unshare = isolated.iter().position(|a| a == "--unshare-net").unwrap();
        let ro_bind = isolated.iter().position(|a| a == "--ro-bind").unwrap();
        assert!(unshare < ro_bind);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_ext_network_opt_in() {
        let denied = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], false, false);
        assert!(!denied.contains("network-outbound"));
        let allowed = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], true, false);
        assert!(allowed.contains("(allow network-outbound)"));
        assert!(allowed.contains("(allow network-inbound)"));
        // The base policy stays identical: deny default with the write root.
        assert!(allowed.starts_with("(version 1)\n(deny default)\n"));
        assert!(allowed.contains("(subpath \"/job\")"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_wrap_profile_confines_signals_to_self() {
        let profile = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], false, true);
        assert!(profile.contains("(allow signal (target self))"));
        assert!(!profile.contains("(allow signal)\n"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_denies_by_default_and_lists_roots() {
        let profile = seatbelt_profile_opts(
            &[PathBuf::from("/skill/dir")],
            &[PathBuf::from("/job/dir"), PathBuf::from("/private/tmp")],
            &[],
            false,
            false,
        );
        // Bash policy keeps the global signal allow; wrap mode narrows it.
        assert!(profile.contains("(allow signal)"));
        assert!(!profile.contains("(target self)"));
        assert!(profile.starts_with("(version 1)\n(deny default)\n"));
        assert!(profile.contains("(allow process-exec)"));
        assert!(profile.contains("(allow process-fork)"));
        // dyld/libsystem abort at exec without global metadata + a readable
        // root dir; see the seatbelt_profile doc comment.
        assert!(profile.contains("(allow file-read-metadata)"));
        assert!(profile.contains("(allow file-read-data\n  (literal \"/\")\n"));
        assert!(profile.contains("(subpath \"/skill/dir\")"));
        assert!(profile.contains("(literal \"/job/dir\")"));
        assert!(profile.contains("(subpath \"/private/tmp\")"));
        // Write allowlist must not contain the read-only skill dir.
        let write_section = profile.split("(allow file-write*").nth(1).unwrap();
        assert!(!write_section.contains("/skill/dir"));
        assert!(write_section.contains("(subpath \"/dev\")"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_escapes_quotes_and_backslashes() {
        let profile =
            seatbelt_profile_opts(&[PathBuf::from("/weird\"quo\\te")], &[], &[], false, false);
        assert!(profile.contains("/weird\\\"quo\\\\te"));
    }

    #[test]
    fn collect_read_write_includes_cwd_session_and_tmp() {
        let cwd = std::env::temp_dir().join("velites-sandbox-test-cwd");
        std::fs::create_dir_all(&cwd).unwrap();
        let session = cwd.join("session");
        std::fs::create_dir_all(&session).unwrap();
        let paths = collect_read_write(&cwd, Some(&session)).unwrap();
        assert!(paths.contains(&cwd.canonicalize().unwrap()));
        assert!(paths.contains(&session.canonicalize().unwrap()));
        assert!(paths.contains(&canonical_or_raw(std::env::temp_dir())));
        assert!(paths.contains(&canonical_or_raw(PathBuf::from("/tmp"))));
        std::fs::remove_dir_all(&cwd).unwrap();
    }

    // --- python3 read-root probe (design §8: python3 must run inside the
    // sandbox; uv-style interpreters load libpython via @rpath from the
    // install prefix, and venv pythons need pyvenv.cfg readable — both go on
    // the read-only whitelist). ---

    /// A fake uv-style install: `<tmp>/uv/python/cpython-3.13/bin/python3.13`
    /// (executable), plus a `.venv/bin/python3` symlink pointing at it.
    fn fake_uv_python() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let prefix = dir
            .path()
            .join("uv/python/cpython-3.13.13-macos-aarch64-none");
        let bin = prefix.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let interpreter = bin.join("python3.13");
        std::fs::write(&interpreter, "fake interpreter").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&interpreter, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        (dir, prefix, interpreter)
    }

    #[test]
    fn python_prefix_from_canonical_computes_install_prefix() {
        let (_dir, prefix, interpreter) = fake_uv_python();
        let canonical = interpreter.canonicalize().unwrap();
        assert_eq!(
            python_prefix_from_canonical(&canonical),
            Some(prefix.canonicalize().unwrap())
        );
    }

    #[test]
    fn python_prefix_from_canonical_rejects_system_and_bogus_paths() {
        // /usr/bin/python3 → prefix /usr: no "python" in the prefix path,
        // and /usr is already covered by the system read list — skip.
        assert_eq!(
            python_prefix_from_canonical(Path::new("/usr/bin/python3")),
            None
        );
        // Nonexistent prefix → skip (never whitelist a phantom path).
        assert_eq!(
            python_prefix_from_canonical(Path::new("/definitely/not/here/python/bin/python3")),
            None
        );
    }

    #[test]
    fn find_executable_on_path_skips_non_executables() {
        let (dir, _prefix, interpreter) = fake_uv_python();
        // Earlier PATH entry has a NON-executable python3 — must be skipped.
        let earlier = dir.path().join("earlier");
        std::fs::create_dir(&earlier).unwrap();
        std::fs::write(earlier.join("python3"), "not executable").unwrap();
        // .venv-style symlink to the real interpreter; returned UNRESOLVED
        // (the caller needs the symlink path for venv detection).
        let venv_bin = dir.path().join("venv/bin");
        std::fs::create_dir_all(&venv_bin).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&interpreter, venv_bin.join("python3")).unwrap();

        let path_var =
            std::ffi::OsString::from(format!("{}:{}", earlier.display(), venv_bin.display()));
        let found = find_executable_on_path(&path_var, "python3")
            .expect("executable python3 must be found");
        assert_eq!(found, venv_bin.join("python3"));
    }

    #[cfg(unix)]
    #[test]
    fn python_read_roots_covers_venv_root_and_base_prefix() {
        let (dir, prefix, interpreter) = fake_uv_python();
        // A venv whose bin/python3 symlinks to the uv interpreter.
        let venv = dir.path().join("project/.venv");
        std::fs::create_dir_all(venv.join("bin")).unwrap();
        std::os::unix::fs::symlink(&interpreter, venv.join("bin/python3")).unwrap();
        std::fs::write(
            venv.join("pyvenv.cfg"),
            format!("home = {}\n", prefix.join("bin").display()),
        )
        .unwrap();

        let path_var = std::ffi::OsString::from(venv.join("bin").to_string_lossy().into_owned());
        let roots = python_read_roots_from_path(&path_var);
        assert_eq!(
            roots,
            vec![venv.canonicalize().unwrap(), prefix.canonicalize().unwrap(),]
        );
    }

    #[cfg(unix)]
    #[test]
    fn python_read_roots_skips_venv_without_pyvenv_cfg() {
        let (dir, prefix, interpreter) = fake_uv_python();
        // A bare bin dir (no pyvenv.cfg) is NOT a venv: only the base
        // install prefix is whitelisted.
        let bare_bin = dir.path().join("tools/bin");
        std::fs::create_dir_all(&bare_bin).unwrap();
        std::os::unix::fs::symlink(&interpreter, bare_bin.join("python3")).unwrap();

        let path_var = std::ffi::OsString::from(bare_bin.to_string_lossy().into_owned());
        let roots = python_read_roots_from_path(&path_var);
        assert_eq!(roots, vec![prefix.canonicalize().unwrap()]);
    }

    #[test]
    fn python_read_roots_empty_without_python3_on_path() {
        let dir = tempfile::tempdir().unwrap();
        let path_var = std::ffi::OsString::from(dir.path().to_string_lossy().into_owned());
        assert!(python_read_roots_from_path(&path_var).is_empty());
    }

    #[cfg(target_os = "macos")]
    #[cfg(unix)]
    #[test]
    fn seatbelt_profile_includes_python_roots_read_only() {
        let (dir, prefix, interpreter) = fake_uv_python();
        let venv = dir.path().join("project/.venv");
        std::fs::create_dir_all(venv.join("bin")).unwrap();
        std::os::unix::fs::symlink(&interpreter, venv.join("bin/python3")).unwrap();
        std::fs::write(
            venv.join("pyvenv.cfg"),
            format!("home = {}\n", prefix.join("bin").display()),
        )
        .unwrap();
        let path_var = std::ffi::OsString::from(venv.join("bin").to_string_lossy().into_owned());
        let roots = python_read_roots_from_path(&path_var);

        let profile = seatbelt_profile_opts(&roots, &[], &[], false, false);
        for root in &roots {
            let escaped = seatbelt_escape(root);
            assert!(profile.contains(&format!("(literal \"{escaped}\")")));
            assert!(profile.contains(&format!("(subpath \"{escaped}\")")));
            // Read-only: the write section must not mention the root.
            let write_section = profile.split("(allow file-write*").nth(1).unwrap();
            assert!(!write_section.contains(escaped.as_str()));
        }
    }

    #[test]
    fn python_read_roots_on_real_path_respects_guards() {
        // Environment-dependent, so only assert the invariant: every probed
        // root is an existing directory.
        for root in python_read_roots() {
            assert!(root.is_dir(), "root must exist: {}", root.display());
        }
    }
}
