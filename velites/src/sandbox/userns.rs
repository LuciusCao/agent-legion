//! Prebuilt user-namespace launcher for non-root, non-setuid bubblewrap.

use std::path::PathBuf;

#[cfg(target_os = "linux")]
use anyhow::Context;
use anyhow::{anyhow, Result};
#[cfg(target_os = "linux")]
use std::os::fd::AsRawFd;

pub struct CommandSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub helper: bool,
}

impl CommandSpec {
    pub fn direct(program: &str, args: &[String]) -> Self {
        Self {
            program: PathBuf::from(program),
            args: args.to_vec(),
            helper: false,
        }
    }

    pub fn command(self) -> std::process::Command {
        let mut command = std::process::Command::new(self.program);
        command.args(self.args);
        if self.helper {
            command
                .env_clear()
                .envs(crate::tools::bash_env::inherited_env());
        }
        command
    }
}

#[cfg(target_os = "linux")]
fn requires_launcher(euid: u32, bwrap_setuid: bool) -> bool {
    euid != 0 && !bwrap_setuid
}

#[cfg(not(target_os = "linux"))]
#[allow(dead_code)]
fn requires_launcher(_euid: u32, _bwrap_setuid: bool) -> bool {
    false
}

#[cfg(target_os = "linux")]
fn resolve_executable(program: &str, cwd: &std::path::Path) -> Result<PathBuf> {
    use std::os::unix::fs::PermissionsExt;

    let candidates = if program.contains('/') {
        vec![PathBuf::from(program)]
    } else {
        std::env::var_os("PATH")
            .map(|paths| {
                std::env::split_paths(&paths)
                    .map(|root| root.join(program))
                    .collect()
            })
            .unwrap_or_default()
    };
    for candidate in candidates {
        let candidate = if candidate.is_absolute() {
            candidate
        } else {
            cwd.join(candidate)
        };
        if let Ok(metadata) = std::fs::metadata(&candidate) {
            if metadata.is_file() && metadata.permissions().mode() & 0o111 != 0 {
                return Ok(std::fs::canonicalize(candidate)?);
            }
        }
    }
    Err(anyhow!("{program} not found or not executable"))
}

#[cfg(target_os = "linux")]
fn launcher_arguments(bwrap: PathBuf, args: &[String]) -> Vec<String> {
    let mut launcher = vec![
        "sandbox".into(),
        "userns-launcher".into(),
        "--".into(),
        bwrap.display().to_string(),
    ];
    launcher.extend(args.iter().cloned());
    launcher
}

pub fn command_spec(
    program: &str,
    args: &[String],
    sandbox_enabled: bool,
    cwd: &std::path::Path,
) -> std::io::Result<CommandSpec> {
    if !sandbox_enabled {
        return Ok(CommandSpec::direct(program, args));
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (program, cwd);
        Ok(CommandSpec::direct(program, args))
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::PermissionsExt;

        let bwrap = resolve_executable(program, cwd).map_err(std::io::Error::other)?;
        let setuid = std::fs::metadata(&bwrap)?.permissions().mode() & 0o4000 != 0;
        if !requires_launcher(unsafe { libc::geteuid() }, setuid) {
            return Ok(CommandSpec::direct(program, args));
        }
        let current = std::env::current_exe()?;
        let launcher_args = launcher_arguments(bwrap, args);
        Ok(CommandSpec {
            program: current,
            args: launcher_args,
            helper: true,
        })
    }
}

#[cfg(target_os = "linux")]
mod fd;
#[cfg(target_os = "linux")]
mod namespace;
#[cfg(target_os = "linux")]
mod process;
#[cfg(target_os = "linux")]
pub(crate) use namespace::prepare_user_namespace;
#[cfg(all(target_os = "linux", test))]
pub(crate) use namespace::{mapping_line, wait_child};
#[cfg(test)]
mod fd_tests;
#[cfg(test)]
mod namespace_tests;
#[cfg(test)]
mod process_tests;

#[cfg(target_os = "linux")]
fn validate_bwrap_args(args: &[String]) -> Result<()> {
    let options_end = args
        .iter()
        .position(|arg| arg == "--")
        .unwrap_or(args.len());
    if args[..options_end].iter().any(|arg| arg == "--userns") {
        return Err(anyhow!("--userns is launcher-owned"));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
pub fn run_launcher(args: &[String]) -> Result<()> {
    use std::os::unix::process::CommandExt;

    let separator = args
        .iter()
        .position(|arg| arg == "--")
        .ok_or_else(|| anyhow!("missing --"))?;
    let bwrap = args
        .get(separator + 1)
        .ok_or_else(|| anyhow!("missing bwrap path"))?;
    let bwrap_args = &args[separator + 2..];
    validate_bwrap_args(bwrap_args)?;
    let namespace = prepare_user_namespace()?;
    let mut command = std::process::Command::new(bwrap);
    command
        .env_clear()
        .envs(crate::tools::bash_env::inherited_env())
        .arg("--userns")
        .arg(namespace.as_raw_fd().to_string())
        .args(bwrap_args);
    let error = command.exec();
    Err(anyhow!(error)).context("exec bwrap")
}

#[cfg(not(target_os = "linux"))]
pub fn run_launcher(_args: &[String]) -> Result<()> {
    Err(anyhow!("userns launcher is only available on Linux"))
}

#[cfg(test)]
mod tests;
