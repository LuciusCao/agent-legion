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
//! - Linux: `bubblewrap` (selective read-only binds: system paths + skill
//!   dirs + the probed python3 roots; read-write binds for
//!   cwd/session/$TMPDIR, tmpfs on /tmp; the bash tool keeps the shared
//!   network and pid namespaces it has always had).
//!
//! Fail-closed: [`Sandbox::new`] probes the backend and returns an error when
//! it is unavailable; the harness refuses to start instead of degrading to an
//! unsandboxed run. The only escape hatch is the explicit `--no-sandbox` flag.

use std::path::{Path, PathBuf};

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
use anyhow::anyhow;
#[cfg(any(target_os = "macos", target_os = "linux"))]
use anyhow::Context;

#[cfg(any(target_os = "macos", test))]
mod macos;

#[cfg(any(target_os = "macos", test))]
use macos::{ancestor_list_only, macos_system_read_paths, seatbelt_profile_opts};

#[cfg(target_os = "macos")]
use macos::probe_macos;

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

/// Linux bubblewrap policy variants. Both use selective read-only binds;
/// the bash tool keeps its historical shared network and pid namespaces,
/// while `sandbox wrap` gets the strict one (private pid namespace,
/// isolated network unless allowed).
#[cfg(target_os = "linux")]
enum BwrapPolicy {
    BashTool {
        read_only: Vec<PathBuf>,
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
    /// Additional read-only roots (canonicalized at build time).
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
    /// directories (read-only).
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
        let mut skill_roots = Vec::new();
        for dir in &options.read_only {
            let canonical = dir
                .canonicalize()
                .with_context(|| format!("failed to canonicalize read root `{}`", dir.display()))?;
            read_only.push(canonical.clone());
            skill_roots.push(canonical);
        }
        // Design §8: python3 must run inside the sandbox (skill scripts).
        // uv/Homebrew interpreters live outside the system read list and
        // load libpython via @rpath from their install prefix, and a venv
        // python dies at startup when pyvenv.cfg is unreadable — whitelist
        // both READ-ONLY. A failed probe is silently skipped (system
        // pythons are covered by macos_system_read_paths already).
        let mut python_roots = Vec::new();
        for root in python_read_roots() {
            if !read_only.contains(&root) {
                read_only.push(root.clone());
                python_roots.push(root);
            }
        }
        let mut list_only = Vec::new();
        match mode {
            SandboxMode::Wrap => {
                for root in options.read_only.iter().chain(options.read_write.iter()) {
                    if let Some(parent) = root.parent() {
                        if !list_only.contains(&parent.to_path_buf()) {
                            list_only.push(parent.to_path_buf());
                        }
                    }
                }
            }
            // The bash tool grants the whole ANCESTOR CHAIN of every
            // whitelist root list-only: without it a sandboxed `ls` of a
            // parent dir fails (or looks empty), which misleads the agent
            // into believing the roots do not exist (2026-08-10 incident:
            // EPERM swallowed by 2>/dev/null read as "missing skill dir",
            // triggering a `find /` full-disk scan).
            SandboxMode::BashTool => {
                for root in skill_roots
                    .iter()
                    .chain(&python_roots)
                    .chain(read_write.iter())
                {
                    ancestor_list_only(root, &read_only, &read_write, &mut list_only);
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
            SandboxMode::BashTool => {
                let mut read_only = Vec::new();
                for dir in &options.read_only {
                    read_only.push(dir.canonicalize().with_context(|| {
                        format!("failed to canonicalize read root `{}`", dir.display())
                    })?);
                }
                // Design §8: python3 must run inside the sandbox. With
                // selective binds an interpreter outside the system roots
                // (uv/Homebrew prefix, venv) no longer starts — whitelist the
                // probed roots read-only, same as macOS. A failed probe is
                // silently skipped.
                for root in python_read_roots() {
                    if !read_only.contains(&root) {
                        read_only.push(root);
                    }
                }
                BwrapPolicy::BashTool {
                    read_only,
                    read_write,
                }
            }
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
/// collect the READ-ONLY roots the sandbox (seatbelt profile / bwrap binds)
/// must whitelist for that interpreter to actually start (design §8: python3
/// must run inside the sandbox). Up to two roots:
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
/// covered by the system read paths on both platforms). Only `python3` is
/// probed — design §8 promises exactly that, nothing more. The probe itself
/// is platform-independent (PATH search + pyvenv.cfg + prefix guards), so
/// uv-style prefixes like `~/.local/share/uv/python/cpython-3.13-...` are
/// found the same way on Linux and macOS.
#[cfg(any(unix, test))]
fn python_read_roots() -> Vec<PathBuf> {
    let Some(path_var) = std::env::var_os("PATH") else {
        return Vec::new();
    };
    python_read_roots_from_path(&path_var)
}

/// PATH-value-taking core of [`python_read_roots`] (tests inject a
/// controlled PATH instead of mutating the process-global one).
#[cfg(any(unix, test))]
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
#[cfg(any(unix, test))]
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
#[cfg(any(unix, test))]
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

#[cfg(any(unix, test))]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    // fs::metadata follows symlinks, so a .venv/bin/python3 symlink is
    // judged by the mode of the real interpreter.
    std::fs::metadata(path)
        .map(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// Canonical target of `path` (meant for /etc/resolv.conf) when it leaves
/// `etc`: systemd-resolved hosts symlink /etc/resolv.conf into
/// /run/systemd/resolve/..., which the selective bind list does not cover —
/// without the extra bind, sandboxed DNS is dead. `etc` is a parameter so
/// tests can point at a fixture tree. None when the file is missing or
/// resolves inside `etc` (already covered by the /etc ro-bind).
#[cfg(any(target_os = "linux", test))]
fn resolv_conf_target_outside(path: &Path, etc: &Path) -> Option<PathBuf> {
    let target = path.canonicalize().ok()?;
    if target.starts_with(etc) {
        return None;
    }
    Some(target)
}

/// Production entry point: probe the real /etc/resolv.conf.
#[cfg(any(target_os = "linux", test))]
fn resolv_conf_read_root() -> Option<PathBuf> {
    resolv_conf_target_outside(Path::new("/etc/resolv.conf"), Path::new("/etc"))
}

/// Append the resolv.conf escape bind (`--ro-bind-try` covers a target that
/// vanished between this probe and exec) when the probe found one.
#[cfg(any(target_os = "linux", test))]
fn push_resolv_conf_bind(argv: &mut Vec<String>) {
    if let Some(target) = resolv_conf_read_root() {
        let display = target.display().to_string();
        argv.extend(["--ro-bind-try".into(), display.clone(), display]);
    }
}

/// `bwrap` argv for the bash tool: selective read-only binds (system roots
/// plus the read-only roots) instead of a blanket `/` bind, read-write
/// binds for the read-write roots. The bash tool keeps its historical
/// differences from the `sandbox wrap` strict policy: no `--unshare-pid`,
/// and the network namespace is shared unless `unshare_net` is requested.
/// `/tmp` becomes an empty tmpfs (scratch writes stay off the host);
/// read-write binds come after it because later mounts win.
#[cfg(any(target_os = "linux", test))]
fn bwrap_argv_opts(
    read_only: &[PathBuf],
    read_write: &[PathBuf],
    inner: &[String],
    unshare_net: bool,
) -> Vec<String> {
    let mut argv: Vec<String> = vec!["--die-with-parent".into()];
    if unshare_net {
        argv.push("--unshare-net".into());
    }
    // Read-only system roots a binary needs to start; missing ones are
    // skipped (e.g. /lib64 on some distros). Same list as bwrap_wrap_argv.
    for system_root in ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt"] {
        if Path::new(system_root).is_dir() {
            argv.extend(["--ro-bind".into(), system_root.into(), system_root.into()]);
        }
    }
    for path in read_only {
        let display = path.display().to_string();
        argv.extend(["--ro-bind".into(), display.clone(), display]);
    }
    push_resolv_conf_bind(&mut argv);
    argv.extend([
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
#[cfg(test)]
fn bwrap_argv(read_only: &[PathBuf], read_write: &[PathBuf], inner: &[String]) -> Vec<String> {
    bwrap_argv_opts(read_only, read_write, inner, false)
}

/// Dispatch the bwrap argv per policy (bash tool vs `sandbox wrap`).
#[cfg(target_os = "linux")]
fn bwrap_argv_for(policy: &BwrapPolicy, inner: &[String]) -> Vec<String> {
    match policy {
        BwrapPolicy::BashTool {
            read_only,
            read_write,
        } => bwrap_argv_opts(read_only, read_write, inner, false),
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
    push_resolv_conf_bind(&mut argv);
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
mod tests;
