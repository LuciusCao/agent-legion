//! macOS seatbelt backend for the OS filesystem sandbox: system read
//! paths, profile generation (deny-default with literal/subpath read
//! grants, list-only ancestor chains), string escaping, and the
//! backend probe. Split from ``sandbox.rs`` for the file size budget
//! (#202); the cross-platform types and the Linux bwrap backend stay there.

#[cfg(any(target_os = "macos", test))]
use std::path::Path;

#[cfg(target_os = "macos")]
use std::path::PathBuf;

#[cfg(target_os = "macos")]
use anyhow::{anyhow, Context};

/// System locations a process must be able to READ to execute at all
/// (binaries, dyld cache, linker config, device nodes, Homebrew prefix).
#[cfg(target_os = "macos")]
pub(crate) fn macos_system_read_paths() -> Vec<PathBuf> {
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
pub(crate) fn seatbelt_escape(path: &Path) -> String {
    path.display()
        .to_string()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
}

/// Collect the LIST-ONLY ancestor chain of a whitelist root: every directory
/// from the root's parent up to (but excluding) `/`, deduped against `out`
/// and against roots that already carry literal+subpath grants. A bare
/// `literal` grant allows readdir on the directory itself while keeping every
/// file inside unreadable — a sandboxed `ls` along the chain sees the next
/// level exist instead of a misleading EPERM/empty listing.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn ancestor_list_only(
    root: &Path,
    read_only: &[PathBuf],
    read_write: &[PathBuf],
    out: &mut Vec<PathBuf>,
) {
    for ancestor in root.ancestors().skip(1) {
        if ancestor.parent().is_none() {
            // "/" already has a (literal "/") grant in the profile.
            continue;
        }
        let ancestor = ancestor.to_path_buf();
        if read_only.contains(&ancestor)
            || read_write.contains(&ancestor)
            || out.contains(&ancestor)
        {
            continue;
        }
        out.push(ancestor);
    }
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
pub(crate) fn seatbelt_profile_opts(
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

#[cfg(target_os = "macos")]
pub(crate) fn probe_macos() -> anyhow::Result<()> {
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
